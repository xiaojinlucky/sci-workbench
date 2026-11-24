#!/usr/bin/env python3
"""Validate formula Markdown rendering, delimiter balance, pseudo-math, and anchor traceability.

Desensitized standalone offline validator adapted from portable Markdown math-rendering guardrails.
Operates strictly with Python 3.12 standard library.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

EQUATION_ID_RE = re.compile(r"^E\d{3,}$")
EQUATION_ANCHOR_RE = re.compile(r"<a\s+(?:id|name)=[\"'](E\d{3,})[\"']\s*></a>", re.IGNORECASE)
GENERIC_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})([^\n]*)$", re.MULTILINE)
LATEX_COMMAND_RE = re.compile(
    r"\\(?:frac|dfrac|tfrac|sum|prod|int|oint|sqrt|begin|end|left|right|"
    r"alpha|beta|gamma|delta|theta|lambda|mu|sigma|omega|tau|partial|nabla|"
    r"mathcal|mathrm|mathbf|operatorname|overline|underline|widetilde|hat)\b"
)
PROSE_PSEUDO_MATH_RE = re.compile(
    r"\((?:[^()\n]*\\[A-Za-z]+[^()\n]*|"
    r"[A-Za-z][A-Za-z0-9]*_(?:\{[^}]+\}|[A-Za-z0-9]+)|"
    r"[A-Za-z][A-Za-z0-9_]{0,6}\s*=\s*[^()\n]{1,30})\)"
)
GREEK_WORD_PAREN_RE = re.compile(
    r"\((?:alpha|beta|gamma|delta|theta|lambda|mu|sigma|tau|omega|Delta\s+[A-Za-z])\)"
)


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str
    line: int | None = None


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _mask_ranges(text: str, ranges: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in ranges:
        for idx in range(max(0, start), min(len(chars), end)):
            if chars[idx] != "\n":
                chars[idx] = " "
    return "".join(chars)


def _fenced_ranges(text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]], list[Finding]]:
    ranges: list[tuple[int, int]] = []
    math_ranges: list[tuple[int, int]] = []
    findings: list[Finding] = []
    open_fence: tuple[str, int, int, int, bool] | None = None

    for match in GENERIC_FENCE_RE.finditer(text):
        marker = match.group(1)
        if open_fence is None:
            lang = match.group(2).strip().split(maxsplit=1)[0].lower() if match.group(2).strip() else ""
            open_fence = (marker[0], len(marker), match.start(), match.end(), lang in {"math", "latex"})
            continue
        char, length, start, body_start, is_math = open_fence
        if marker[0] == char and len(marker) >= length:
            ranges.append((start, match.end()))
            if is_math:
                math_ranges.append((start, match.end()))
                if not text[body_start:match.start()].strip():
                    findings.append(Finding("FAIL", "EMPTY_DISPLAY_MATH", "Fenced math block is empty.", _line_number(text, start)))
            open_fence = None

    if open_fence is not None:
        findings.append(Finding("FAIL", "UNCLOSED_FENCE", "Markdown contains an unclosed fenced block.", _line_number(text, open_fence[2])))
        ranges.append((open_fence[2], len(text)))
    return ranges, math_ranges, findings


def _display_math_ranges(text: str) -> tuple[list[tuple[int, int]], list[Finding]]:
    ranges: list[tuple[int, int]] = []
    findings: list[Finding] = []
    tokens = list(re.finditer(r"(?<!\\)\$\$", text))
    if len(tokens) % 2:
        findings.append(Finding("FAIL", "UNBALANCED_DISPLAY_MATH", "Display-math delimiter '$$' is not balanced.", _line_number(text, tokens[-1].start())))
    for idx in range(0, len(tokens) - 1, 2):
        start = tokens[idx].start()
        end = tokens[idx + 1].end()
        body = text[tokens[idx].end():tokens[idx + 1].start()].strip()
        if not body:
            findings.append(Finding("FAIL", "EMPTY_DISPLAY_MATH", "Display-math block '$$...$$' is empty.", _line_number(text, start)))
        ranges.append((start, end))
    return ranges, findings


def validate_formula_markdown(text: str, *, strict: bool = False) -> dict[str, Any]:
    """Validate formula_notes.md for delimiter balance, bare LaTeX, pseudo-math, and anchor order."""
    findings: list[Finding] = []
    fence_ranges, math_fence_ranges, fence_findings = _fenced_ranges(text)
    findings.extend(fence_findings)
    without_fences = _mask_ranges(text, fence_ranges)

    display_ranges, display_findings = _display_math_ranges(without_fences)
    findings.extend(display_findings)

    # Check duplicate display equations
    seen_displays: dict[str, list[int]] = {}
    for start, end in display_ranges:
        body = without_fences[start + 2:end - 2].strip()
        norm = re.sub(r"\s+", "", body).rstrip(",.;")
        if norm:
            seen_displays.setdefault(norm, []).append(start)
    for offsets in seen_displays.values():
        if len(offsets) > 1:
            findings.append(Finding("WARN", "DUPLICATE_DISPLAY_EQUATION", "Identical display equation appears more than once.", _line_number(text, offsets[1])))

    prose = _mask_ranges(without_fences, display_ranges)

    # Check inline $ balance
    inline_tokens = list(re.finditer(r"(?<!\\)(?<!\$)\$(?!\$)", prose))
    if len(inline_tokens) % 2:
        findings.append(Finding("FAIL", "UNBALANCED_INLINE_MATH", "Inline-math delimiter '$' is not balanced.", _line_number(text, inline_tokens[-1].start())))

    inline_ranges = [
        (inline_tokens[i].start(), inline_tokens[i + 1].end())
        for i in range(0, len(inline_tokens) - 1, 2)
    ]
    # Also mask inline backtick code spans so filenames/CLI flags are not misread as pseudo-math
    code_spans = [(m.start(), m.end()) for m in re.finditer(r"`[^`\n]+`", prose)]
    plain_prose = _mask_ranges(prose, inline_ranges + code_spans)

    for match in LATEX_COMMAND_RE.finditer(plain_prose):
        findings.append(
            Finding("FAIL", "BARE_LATEX", f"LaTeX command '{match.group(0)}' appears outside math delimiters.", _line_number(text, match.start()))
        )

    for match in PROSE_PSEUDO_MATH_RE.finditer(plain_prose):
        findings.append(
            Finding("FAIL", "PARENTHESIZED_PSEUDO_MATH", f"Use $...$ delimiters instead of parentheses for math: {match.group(0)!r}.", _line_number(text, match.start()))
        )
    for match in GREEK_WORD_PAREN_RE.finditer(plain_prose):
        findings.append(
            Finding("FAIL", "PARENTHESIZED_PSEUDO_MATH", f"Use $...$ delimiters instead of parentheses for Greek symbol: {match.group(0)!r}.", _line_number(text, match.start()))
        )

    # Check tab characters in prose (possible corrupted \tau or \theta)
    for line_no, line in enumerate(plain_prose.splitlines(), start=1):
        if "\t" in line:
            findings.append(Finding("FAIL", "TAB_IN_MATH_PROSE", "Literal tab found in prose (possible corrupted \\tau/\\theta escape).", line_no))

    anchors = EQUATION_ANCHOR_RE.findall(text)
    dup_anchors = sorted({a for a in anchors if anchors.count(a) > 1})
    for eq_id in dup_anchors:
        findings.append(Finding("FAIL", "DUPLICATE_EQUATION_ID", f"Duplicate equation anchor: {eq_id}."))
    if anchors:
        nums = [int(a[1:]) for a in anchors]
        if nums != list(range(1, len(nums) + 1)):
            findings.append(Finding("WARN", "NONSEQUENTIAL_EQUATION_IDS", "Equation anchors should follow reading order E001, E002, ..."))

    display_count = len(display_ranges) + len(math_fence_ranges)
    if display_count > 0 and not anchors:
        findings.append(Finding("FAIL", "MISSING_EQUATION_ANCHORS", "Display equations exist without matching E001... anchors."))
    if display_count >= 3 and not re.search(r"^#{1,6}\s+(?:公式索引|Equation Index)\s*$", text, re.MULTILINE | re.IGNORECASE):
        findings.append(Finding("WARN", "MISSING_EQUATION_INDEX", "Three or more display equations should include an Equation Index section."))

    failed = any(f.severity == "FAIL" or (strict and f.severity == "WARN") for f in findings)
    return {
        "ready": not failed,
        "strict": strict,
        "display_equation_count": display_count,
        "anchor_count": len(anchors),
        "summary": {
            "fail": sum(1 for f in findings if f.severity == "FAIL"),
            "warn": sum(1 for f in findings if f.severity == "WARN"),
        },
        "findings": [asdict(f) for f in findings],
    }


def self_test() -> int:
    good_md = """# Formula Notes

<a id="E001"></a>
**Source:** source-formulas.xml · `eq_1`

$$
I_0 = \\frac{4 E_0}{\\pi w_0^2 \\tau}
$$

其中 $I_0$ 为峰值强度，$E_0$ 为脉冲能量，$w_0$ 为焦斑半径，$\\tau$ 为脉宽。
"""
    res_ok = validate_formula_markdown(good_md, strict=True)
    if not res_ok["ready"]:
        print(f"Self-test failed: valid markdown rejected: {res_ok}", file=sys.stderr)
        return 1

    bad_md = """# Broken
<a id="E001"></a>
Raw formula: \\frac{a}{b}
说明：其中 (I_0) 与 (tau) 未用美元符包裹。
$$
x + y
"""
    res_bad = validate_formula_markdown(bad_md, strict=False)
    codes = {f["code"] for f in res_bad["findings"] if f["severity"] == "FAIL"}
    expected = {"BARE_LATEX", "UNBALANCED_DISPLAY_MATH", "PARENTHESIZED_PSEUDO_MATH"}
    if not expected.issubset(codes):
        print(f"Self-test failed: missing codes {sorted(expected - codes)}", file=sys.stderr)
        return 1

    print("Self-test passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate formula_notes.md Markdown math rendering.")
    parser.add_argument("markdown", nargs="?", type=Path, help="Path to formula_notes.md")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON output")
    parser.add_argument("--self-test", action="store_true", help="Run built-in valid/invalid checks")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.markdown is None:
        parser.error("markdown path is required unless --self-test is used")

    text = args.markdown.read_text(encoding="utf-8")
    payload = validate_formula_markdown(text, strict=args.strict)
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in payload["findings"]:
            loc = f" line {item['line']}" if item.get("line") else ""
            print(f"[{item['severity']}] {item['code']}{loc}: {item['message']}")
        print(f"Ready: {'yes' if payload['ready'] else 'no'}")
    return 0 if payload["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
