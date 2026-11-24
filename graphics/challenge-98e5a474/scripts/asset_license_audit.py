#!/usr/bin/env python3
"""Scientific figure asset & icon license provenance auditor (DrawPlotia/OpenBioFigure compliance gate)."""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as _StdET
from pathlib import Path
from typing import Any

try:
    import defusedxml.ElementTree as _SafeET  # type: ignore
except ImportError:
    _SafeET = None

ALLOWED_LICENSES = {
    "CC0",
    "CC0-1.0",
    "CC-BY-4.0",
    "CC-BY-3.0",
    "CC-BY-SA-4.0",
    "CC-BY-SA-3.0",
    "MIT",
    "APACHE-2.0",
    "BSD-3-CLAUSE",
    "BSD-2-CLAUSE",
    "PUBLIC DOMAIN",
    "PUBLIC-DOMAIN",
    "SELF-AUTHORED",
    "ORIGINAL",
}

RESTRICTED_TOKENS = (
    "ALL RIGHTS RESERVED",
    "PROPRIETARY",
    "UNKNOWN",
    "UNSPECIFIED",
    "TBD",
    "NON-COMMERCIAL",
    "CC-BY-NC",
)

_FORBIDDEN_XML = re.compile(r"<!(?:DOCTYPE|ENTITY|ELEMENT|ATTLIST|NOTATION)\b", re.IGNORECASE)


def _norm_license(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip()).upper()


def _safe_parse(path: Path) -> _StdET.Element:
    raw = path.read_bytes()
    if _SafeET is not None:
        return _SafeET.fromstring(raw)
    if _FORBIDDEN_XML.search(raw[:32768].decode("utf-8", errors="ignore")):
        raise ValueError("Refused SVG with DOCTYPE/ENTITY declarations")
    return _StdET.fromstring(raw)


def audit_asset_licenses(
    spec: dict[str, Any] | None,
    *,
    svg_path: Path | None = None,
) -> dict[str, Any]:
    """Audit declared icon/asset licenses and cross-check SVG data-asset-id references."""
    errors: list[str] = []
    warnings: list[str] = []
    manifest: list[dict[str, str]] = []
    declared_ids: set[str] = set()

    assets = (spec or {}).get("assets") or []
    for idx, item in enumerate(assets):
        if not isinstance(item, dict):
            errors.append(f"Asset entry #{idx+1} must be a JSON object")
            continue
        aid = str(item.get("id") or f"asset-{idx+1}").strip()
        declared_ids.add(aid)
        lic_raw = str(item.get("license") or "").strip()
        lic_norm = _norm_license(lic_raw)
        author = str(item.get("author") or "").strip()
        source = str(item.get("source") or "").strip()
        version = str(item.get("version") or "1.0").strip()

        status = "approved"
        if not lic_raw:
            status = "blocked"
            errors.append(f"ASSET_LICENSE_MISSING: asset {aid!r} has no declared license")
        elif any(tok in lic_norm for tok in RESTRICTED_TOKENS):
            status = "blocked"
            errors.append(f"ASSET_LICENSE_RESTRICTED: asset {aid!r} license {lic_raw!r} is not cleared for open scientific reuse")
        elif lic_norm not in ALLOWED_LICENSES:
            status = "blocked"
            errors.append(f"ASSET_LICENSE_UNVERIFIED: asset {aid!r} license {lic_raw!r} is not in approved SPDX/CC list")

        if lic_norm not in ("SELF-AUTHORED", "ORIGINAL", "CC0", "CC0-1.0", "PUBLIC DOMAIN", "PUBLIC-DOMAIN"):
            if not author:
                errors.append(f"ASSET_ATTRIBUTION_MISSING: asset {aid!r} ({lic_raw}) requires 'author' attribution")
                status = "blocked"
            if not source:
                warnings.append(f"Asset {aid!r} ({lic_raw}) lacks upstream 'source' reference URL/DOI")

        manifest.append({
            "id": aid,
            "author": author or "Self-Authored",
            "license": lic_raw or "MISSING",
            "source": source or "internal-vector-primitive",
            "version": version,
            "status": status,
        })

    svg_asset_refs: list[str] = []
    if svg_path is not None and svg_path.is_file():
        root = _safe_parse(svg_path)
        for el in root.iter():
            aref = (el.get("data-asset-id") or "").strip()
            if aref:
                svg_asset_refs.append(aref)
                if aref not in declared_ids:
                    errors.append(f"UNDECLARED_SVG_ASSET: SVG references data-asset-id={aref!r} not listed in spec.assets")
            # Catch unattributed external hrefs on non-anchor elements
            for k, v in el.attrib.items():
                if k.rsplit("}", 1)[-1] == "href" and v and not v.startswith("#"):
                    if el.tag.rsplit("}", 1)[-1] != "a":
                        errors.append(f"EXTERNAL_UNAUDITED_HREF: {el.tag.rsplit('}', 1)[-1]} references external {v!r}")

    return {
        "passed": len(errors) == 0,
        "declared_asset_count": len(manifest),
        "svg_asset_ref_count": len(svg_asset_refs),
        "manifest": manifest,
        "blocking_errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit scientific schematic asset licenses and provenance.")
    parser.add_argument("--spec", default="", help="Path to schematic_spec.json containing optional 'assets' list")
    parser.add_argument("--svg", default="", help="Optional SVG path to cross-check data-asset-id references")
    parser.add_argument("--out", default="", help="Optional license manifest JSON output")
    args = parser.parse_args(argv)

    spec_obj = json.loads(Path(args.spec).read_text(encoding="utf-8-sig")) if args.spec else None
    svg_p = Path(args.svg) if args.svg else None
    rep = audit_asset_licenses(spec_obj, svg_path=svg_p)
    encoded = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
