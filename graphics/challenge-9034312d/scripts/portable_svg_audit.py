#!/usr/bin/env python3
"""Zero-cairosvg portable & editable SVG structural verifier (adapted from deathcats4/reconstruct-editable-figure, MIT)."""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as _StdET
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    import defusedxml.ElementTree as _SafeET
except ImportError:
    _SafeET = None

_FORBIDDEN_XML = re.compile(r"<!(?:DOCTYPE|ENTITY|ELEMENT|ATTLIST|NOTATION)\b", re.IGNORECASE)
_ANY_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
_ABS_WIN_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SAFE_RASTER_PREFIXES = (
    "data:image/png;base64,",
    "data:image/jpeg;base64,",
    "data:image/gif;base64,",
    "data:image/webp;base64,",
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def safe_parse_svg(svg_path: str | Path) -> _StdET.Element:
    raw = Path(svg_path).read_bytes()
    if _SafeET is not None:
        return _SafeET.fromstring(raw)
    if _FORBIDDEN_XML.search(raw[:32768].decode("utf-8", errors="ignore")):
        raise ValueError("Refused SVG containing DOCTYPE/ENTITY declarations")
    return _StdET.fromstring(raw)


def _uri_issue(val: str, *, allow_raster: bool) -> str | None:
    ref = val.strip().strip("'\"")
    if not ref or ref.startswith("#"):
        return None
    low = ref.lower()
    if low.startswith("data:"):
        if allow_raster and low.startswith(_SAFE_RASTER_PREFIXES):
            return None
        return "unsupported data URI"
    if low.startswith("//") or _ABS_WIN_RE.match(ref):
        return "external or host path URI"
    parsed = urlsplit(ref.replace("\\", "/"))
    if parsed.scheme:
        return f"external URI scheme {parsed.scheme!r}"
    if parsed.path.startswith("/") or ".." in parsed.path.split("/"):
        return "absolute or parent-traversing local resource"
    return "non-local external resource"


def audit_portable_svg(
    svg_path: str | Path,
    *,
    allow_embedded_raster: bool = False,
    strict_pptx_portability: bool = False,
) -> dict[str, Any]:
    root = safe_parse_svg(svg_path)
    if _local(root.tag) != "svg":
        raise ValueError(f"Root tag must be <svg>, got <{_local(root.tag)}>")

    blockers: list[str] = []
    warnings: list[str] = []
    counts = {
        "layers": 0,
        "groups": 0,
        "paths": 0,
        "primitives": 0,
        "editable_texts": 0,
        "images": 0,
        "markers": 0,
        "uses": 0,
    }
    ids_seen: set[str] = set()
    dup_ids: list[str] = []
    defined_ids: set[str] = set()
    referenced_ids: set[str] = set()
    font_families: set[str] = set()
    has_cjk_text = False
    has_non_ascii_text = False
    cjk_missing_fallback: list[str] = []
    _CJK_FONTS = ("noto sans cjk", "wenquanyi", "source han", "yahei", "simhei", "simsun", "pingfang", "heiti", "songti", "dengxian", "cjk")

    for elem in root.iter():
        tag = _local(elem.tag)
        low_tag = tag.lower()
        eid = elem.get("id")
        if eid:
            defined_ids.add(eid)
            if eid in ids_seen:
                dup_ids.append(eid)
            ids_seen.add(eid)

        if low_tag in {"script", "foreignobject"}:
            blockers.append(f"prohibited active element <{tag}>")
        if low_tag == "g":
            counts["groups"] += 1
            if (eid or "").startswith("layer-") or elem.get("data-layer-name"):
                counts["layers"] += 1
        elif low_tag == "path":
            counts["paths"] += 1
        elif low_tag in {"rect", "circle", "ellipse", "line", "polyline", "polygon"}:
            counts["primitives"] += 1
        elif low_tag == "text":
            txt_content = "".join(elem.itertext()).strip()
            if txt_content:
                counts["editable_texts"] += 1
                ff = (elem.get("font-family") or "").strip()
                if ff:
                    font_families.add(ff)
                if any(ord(ch) > 127 for ch in txt_content):
                    has_non_ascii_text = True
                if any("\u4e00" <= ch <= "\u9fff" for ch in txt_content):
                    has_cjk_text = True
                    ff_low = ff.lower()
                    if not any(cf in ff_low for cf in _CJK_FONTS):
                        cjk_missing_fallback.append(eid or txt_content[:24])
        elif low_tag == "image":
            counts["images"] += 1
            if not allow_embedded_raster:
                blockers.append(f"embedded raster <image id={eid!r}> violates vector-only editability")
        elif low_tag == "marker":
            counts["markers"] += 1
            if strict_pptx_portability:
                blockers.append(f"<marker id={eid!r}> disallowed in strict PPTX-flattening mode")
        elif low_tag == "use":
            counts["uses"] += 1
            if strict_pptx_portability:
                blockers.append(f"<use id={eid!r}> disallowed in strict PPTX-flattening mode")

        for k, v in elem.attrib.items():
            aname = _local(k).lower()
            if aname.startswith("on"):
                blockers.append(f"event handler attribute {aname!r} on <{tag}>")
            if aname == "href":
                if v.startswith("#"):
                    referenced_ids.add(v[1:])
                elif low_tag != "a":
                    reason = _uri_issue(v, allow_raster=(low_tag == "image" and allow_embedded_raster))
                    if reason:
                        blockers.append(f"unsafe href on <{tag}>: {reason}")
            for m in _ANY_URL_RE.finditer(v):
                inner = m.group(2).strip()
                if inner.startswith("#"):
                    referenced_ids.add(inner[1:])
                else:
                    reason = _uri_issue(inner, allow_raster=False)
                    if reason:
                        blockers.append(f"unsafe CSS url() on <{tag}>: {reason}")

    unresolved = sorted(referenced_ids - defined_ids)
    if unresolved:
        blockers.append(f"unresolved local url(#...) references: {unresolved[:10]}")
    if dup_ids:
        warnings.append(f"duplicate element IDs found: {sorted(set(dup_ids))[:10]}")
    if not root.get("viewBox"):
        warnings.append("SVG missing explicit viewBox attribute")
    if not (root.get("width") and root.get("height")):
        warnings.append("SVG missing explicit width/height attributes")
    if counts["editable_texts"] == 0:
        blockers.append("SVG has 0 nonempty editable <text> nodes (labels may be outlined or missing)")
    if counts["paths"] + counts["primitives"] == 0:
        blockers.append("SVG has 0 vector shapes or paths")
    if counts["markers"] > 0 and not strict_pptx_portability:
        warnings.append(f"{counts['markers']} <marker> defs used (native for draw.io/SVG; flatten arrowheads if exporting to PPTX)")
    if has_cjk_text and cjk_missing_fallback:
        warnings.append(
            f"CJK_FONT_FALLBACK_WARNING: {len(cjk_missing_fallback)} CJK <text> node(s) lack explicit CJK font fallback in font-family: {cjk_missing_fallback[:5]}"
        )

    return {
        "passed": len(blockers) == 0,
        "viewBox": root.get("viewBox"),
        "width": root.get("width"),
        "height": root.get("height"),
        "counts": counts,
        "font_audit": {
            "font_families": sorted(font_families),
            "has_non_ascii_text": has_non_ascii_text,
            "has_cjk_text": has_cjk_text,
            "cjk_missing_fallback": cjk_missing_fallback[:10],
        },
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit SVG editability and structural safety (zero-cairosvg).")
    parser.add_argument("svg", help="Input .svg path")
    parser.add_argument("--allow-embedded-raster", action="store_true")
    parser.add_argument("--strict-pptx", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    try:
        report = audit_portable_svg(
            args.svg,
            allow_embedded_raster=args.allow_embedded_raster,
            strict_pptx_portability=args.strict_pptx,
        )
    except Exception as exc:
        report = {"passed": False, "blockers": [str(exc)], "warnings": [], "counts": {}}
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
