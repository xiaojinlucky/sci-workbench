#!/usr/bin/env python3
"""Render SVG to PNG and vector PDF without PyMuPDF/CairoSVG (resvg_py / rsvg-convert / pdftoppm / Matplotlib fallback)."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import warnings
import xml.etree.ElementTree as _StdET
from pathlib import Path
from typing import Any

try:
    import defusedxml.ElementTree as _SafeET
except ImportError:
    _SafeET = None

_FORBIDDEN_XML = re.compile(r"<!(?:DOCTYPE|ENTITY|ELEMENT|ATTLIST|NOTATION)\b", re.IGNORECASE)


def _safe_parse(svg_path: Path) -> _StdET.Element:
    raw = svg_path.read_bytes()
    if _SafeET is not None:
        return _SafeET.fromstring(raw)
    if _FORBIDDEN_XML.search(raw[:32768].decode("utf-8", errors="ignore")):
        raise ValueError("Refused XML containing DOCTYPE/ENTITY")
    return _StdET.fromstring(raw)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _preview_svg_with_preserved_inline_space(svg_path: Path) -> tuple[Path, int, str | None]:
    """Add xml:space only to flat inline text runs with authored edge spaces.

    The transformed SVG is a render-only sibling of the source, so existing
    relative resources keep the same base directory. The deliverable SVG is
    never rewritten.
    """
    try:
        root = _safe_parse(svg_path)
    except (ValueError, _StdET.ParseError) as exc:
        return svg_path, 0, f"preservation parse skipped: {exc}"
    parent_map = {child: parent for parent in root.iter() for child in parent}
    changed = 0

    for elem in root.iter():
        children = list(elem)
        if _local(elem.tag) != "text" or len(children) < 2:
            continue
        if any(_local(child.tag) != "tspan" or len(child) for child in children):
            continue
        if any(
            "\n" in run or "\r" in run
            for run in [elem.text or ""] + [
                value or ""
                for child in children
                for value in (child.text, child.tail)
            ]
        ):
            continue
        if any(name in elem.attrib for name in ("dx", "dy", "rotate", "textLength", "lengthAdjust")):
            continue
        if any(
            name in child.attrib
            for child in children
            for name in ("x", "y", "dx", "dy", "rotate")
        ):
            continue
        if any(
            value and len(re.split(r"[\s,]+", value.strip())) > 1
            for name in ("x", "y")
            for value in [elem.get(name)]
        ):
            continue

        ancestor = elem
        explicit_space = False
        while ancestor is not None:
            if _XML_SPACE in ancestor.attrib:
                explicit_space = True
                break
            ancestor = parent_map.get(ancestor)
        if explicit_space or any(_XML_SPACE in child.attrib for child in children):
            continue

        runs = [elem.text or ""] + [
            value or ""
            for child in children
            for value in (child.text, child.tail)
        ]
        has_authored_edge_space = any(
            run.strip() and run != run.strip()
            for run in runs
        )
        if has_authored_edge_space:
            elem.set(_XML_SPACE, "preserve")
            changed += 1

    if not changed:
        return svg_path, 0, None

    source_parent = svg_path.resolve().parent
    svg_bytes = _StdET.tostring(root, encoding="utf-8", xml_declaration=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{svg_path.stem}.preview-",
            suffix=".svg",
            dir=source_parent,
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(svg_bytes)
    except OSError as exc:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        return svg_path, 0, f"preservation temp unavailable: {exc}"
    assert tmp_path is not None
    if tmp_path.resolve().parent != source_parent:
        tmp_path.unlink(missing_ok=True)
        return svg_path, 0, "preservation temp failed the source-directory check"
    return tmp_path, changed, None


def _num(val: str | None, default: float = 0.0) -> float:
    if not val:
        return default
    m = re.match(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)", str(val))
    return float(m.group(1)) if m else default


def _parse_style(elem: _StdET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    st = elem.get("style") or ""
    for part in st.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip()
    for k in ("fill", "stroke", "stroke-width", "stroke-dasharray", "opacity", "fill-opacity",
              "font-size", "font-weight", "font-family", "text-anchor", "marker-end", "marker-start"):
        if elem.get(k) is not None and k not in out:
            out[k] = elem.get(k, "")
    return out


def _render_via_matplotlib(svg_path: Path, png_path: Path | None, pdf_path: Path | None, dpi: int = 200) -> dict[str, Any]:
    """Deterministic pure-Matplotlib mirror renderer for structured scientific SVGs."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Ellipse, FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle
    from matplotlib.path import Path as MplPath
    from matplotlib.patches import PathPatch

    root = _safe_parse(svg_path)
    vb = (root.get("viewBox") or "").replace(",", " ").split()
    if len(vb) == 4:
        vx, vy, vw, vh = [float(x) for x in vb]
    else:
        vx, vy = 0.0, 0.0
        vw = _num(root.get("width"), 800.0)
        vh = _num(root.get("height"), 600.0)
    vw = max(vw, 1.0)
    vh = max(vh, 1.0)

    w_in = max(2.0, min(14.0, vw / 72.0))
    h_in = w_in * (vh / vw)
    plt.rcParams["pdf.fonttype"] = 42
    plt.rcParams["ps.fonttype"] = 42
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in "".join(root.itertext()))
    try:
        import matplotlib.font_manager as _fm
        installed = {f.name for f in _fm.fontManager.ttflist}
    except Exception:
        installed = set()
    cjk_cands = ["Microsoft YaHei", "SimHei", "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "DengXian", "SimSun", "Noto Sans CJK SC"]
    latin_cands = ["Arial", "Liberation Sans", "DejaVu Sans"]
    cjk_avail = [f for f in cjk_cands if f in installed]
    if has_cjk and cjk_avail:
        plt.rcParams["font.sans-serif"] = cjk_avail + latin_cands
    else:
        plt.rcParams["font.sans-serif"] = latin_cands + (cjk_avail or cjk_cands)

    fig = plt.figure(figsize=(w_in, h_in), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(vx, vx + vw)
    ax.set_ylim(vy + vh, vy)  # SVG Y-down coordinates
    ax.axis("off")

    def _color(c: str | None, default: str | None) -> str | None:
        if c is None or c == "":
            return default
        if c.lower() in ("none", "transparent") or c.startswith("url("):
            return "none"
        return c

    def _walk(node: _StdET.Element, tx: float, ty: float, sx: float, sy: float, z_acc: list[int]) -> None:
        tag = _local(node.tag)
        tr = node.get("transform") or ""
        ntx, nty, nsx, nsy = tx, ty, sx, sy
        m_t = re.search(r"translate\(\s*([+-]?[\d.eE+-]+)(?:[\s,]+([+-]?[\d.eE+-]+))?\s*\)", tr)
        if m_t:
            ntx += float(m_t.group(1)) * sx
            nty += float(m_t.group(2) or 0.0) * sy
        m_s = re.search(r"scale\(\s*([+-]?[\d.eE+-]+)(?:[\s,]+([+-]?[\d.eE+-]+))?\s*\)", tr)
        if m_s:
            fsx = float(m_s.group(1))
            fsy = float(m_s.group(2)) if m_s.group(2) else fsx
            nsx *= fsx
            nsy *= fsy

        if tag in ("defs", "metadata", "title", "desc"):
            return

        st = _parse_style(node)
        fc = _color(st.get("fill"), "none" if tag in ("line", "polyline") else "#000000")
        ec = _color(st.get("stroke"), "none")
        lw = max(0.3, _num(st.get("stroke-width"), 1.0) * 0.75 * max(nsx, 0.2))
        alpha = max(0.0, min(1.0, _num(st.get("opacity"), 1.0)))
        ls = "--" if st.get("stroke-dasharray") and st.get("stroke-dasharray") != "none" else "-"
        z_acc[0] += 1
        z = z_acc[0]

        if tag == "rect":
            x = ntx + _num(node.get("x")) * nsx
            y = nty + _num(node.get("y")) * nsy
            w = _num(node.get("width")) * nsx
            h = _num(node.get("height")) * nsy
            rx = _num(node.get("rx")) * nsx
            if rx > 0:
                ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={rx}",
                                            fc=fc, ec=ec, lw=lw, ls=ls, alpha=alpha, zorder=z))
            else:
                ax.add_patch(Rectangle((x, y), w, h, fc=fc, ec=ec, lw=lw, ls=ls, alpha=alpha, zorder=z))
        elif tag == "circle":
            cx = ntx + _num(node.get("cx")) * nsx
            cy = nty + _num(node.get("cy")) * nsy
            r = _num(node.get("r")) * nsx
            ax.add_patch(Circle((cx, cy), r, fc=fc, ec=ec, lw=lw, ls=ls, alpha=alpha, zorder=z))
        elif tag == "ellipse":
            cx = ntx + _num(node.get("cx")) * nsx
            cy = nty + _num(node.get("cy")) * nsy
            rx = _num(node.get("rx")) * nsx
            ry = _num(node.get("ry")) * nsy
            ax.add_patch(Ellipse((cx, cy), 2 * rx, 2 * ry, fc=fc, ec=ec, lw=lw, ls=ls, alpha=alpha, zorder=z))
        elif tag == "line":
            x1 = ntx + _num(node.get("x1")) * nsx
            y1 = nty + _num(node.get("y1")) * nsy
            x2 = ntx + _num(node.get("x2")) * nsx
            y2 = nty + _num(node.get("y2")) * nsy
            mend = (st.get("marker-end") or "").lower()
            col = ec if ec != "none" else "#333333"
            if "inhibit" in mend or "tbar" in mend:
                ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="|-|,widthA=0.0,widthB=0.45",
                                             mutation_scale=8, lw=lw, color=col, linestyle=ls, alpha=alpha, zorder=z))
            elif mend:
                ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=9,
                                             lw=lw, color=col, linestyle=ls, alpha=alpha, zorder=z))
            else:
                ax.plot([x1, x2], [y1, y2], color=col, lw=lw, ls=ls, alpha=alpha, zorder=z)
        elif tag in ("polygon", "polyline"):
            pts_raw = re.findall(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", node.get("points") or "")
            pts = [(ntx + float(pts_raw[i]) * nsx, nty + float(pts_raw[i + 1]) * nsy)
                   for i in range(0, len(pts_raw) - 1, 2)]
            if len(pts) >= 2:
                if tag == "polygon":
                    ax.add_patch(Polygon(pts, closed=True, fc=fc, ec=ec, lw=lw, ls=ls, alpha=alpha, zorder=z))
                else:
                    ax.plot([p[0] for p in pts], [p[1] for p in pts], color=ec if ec != "none" else "#333333",
                            lw=lw, ls=ls, alpha=alpha, zorder=z)
        elif tag == "path":
            d = node.get("d") or ""
            toks = re.findall(r"[A-Za-z]|[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", d)
            verts: list[tuple[float, float]] = []
            codes: list[int] = []
            i = 0
            cmd = ""
            cx0, cy0 = 0.0, 0.0
            while i < len(toks):
                if toks[i].isalpha():
                    cmd = toks[i]
                    i += 1
                    if cmd in ("Z", "z") and verts:
                        verts.append(verts[0])
                        codes.append(MplPath.CLOSEPOLY)
                    continue
                if cmd in ("M", "L", "m", "l") and i + 1 < len(toks):
                    px, py = float(toks[i]), float(toks[i + 1])
                    i += 2
                    if cmd in ("m", "l"):
                        px += cx0
                        py += cy0
                    cx0, cy0 = px, py
                    verts.append((ntx + px * nsx, nty + py * nsy))
                    codes.append(MplPath.MOVETO if cmd in ("M", "m") else MplPath.LINETO)
                    if cmd == "m":
                        cmd = "l"
                    elif cmd == "M":
                        cmd = "L"
                elif cmd in ("H", "h"):
                    px = float(toks[i]) + (cx0 if cmd == "h" else 0.0)
                    i += 1
                    cx0 = px
                    verts.append((ntx + px * nsx, nty + cy0 * nsy))
                    codes.append(MplPath.LINETO)
                elif cmd in ("V", "v"):
                    py = float(toks[i]) + (cy0 if cmd == "v" else 0.0)
                    i += 1
                    cy0 = py
                    verts.append((ntx + cx0 * nsx, nty + py * nsy))
                    codes.append(MplPath.LINETO)
                elif cmd in ("C", "c") and i + 5 < len(toks):
                    dx = cx0 if cmd == "c" else 0.0
                    dy = cy0 if cmd == "c" else 0.0
                    p1 = (ntx + (float(toks[i]) + dx) * nsx, nty + (float(toks[i + 1]) + dy) * nsy)
                    p2 = (ntx + (float(toks[i + 2]) + dx) * nsx, nty + (float(toks[i + 3]) + dy) * nsy)
                    ex, ey = float(toks[i + 4]) + dx, float(toks[i + 5]) + dy
                    p3 = (ntx + ex * nsx, nty + ey * nsy)
                    cx0, cy0 = ex, ey
                    i += 6
                    verts.extend([p1, p2, p3])
                    codes.extend([MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4])
                elif cmd in ("Q", "q") and i + 3 < len(toks):
                    dx = cx0 if cmd == "q" else 0.0
                    dy = cy0 if cmd == "q" else 0.0
                    p1 = (ntx + (float(toks[i]) + dx) * nsx, nty + (float(toks[i + 1]) + dy) * nsy)
                    ex, ey = float(toks[i + 2]) + dx, float(toks[i + 3]) + dy
                    p2 = (ntx + ex * nsx, nty + ey * nsy)
                    cx0, cy0 = ex, ey
                    i += 4
                    verts.extend([p1, p2])
                    codes.extend([MplPath.CURVE3, MplPath.CURVE3])
                else:
                    i += 1
            if verts:
                mend = (st.get("marker-end") or "").lower()
                if mend and fc == "none" and len(verts) >= 2:
                    col = ec if ec != "none" else "#333333"
                    if "inhibit" in mend or "tbar" in mend:
                        ax.add_patch(FancyArrowPatch(verts[0], verts[-1], arrowstyle="|-|,widthA=0.0,widthB=0.45",
                                                     mutation_scale=8, lw=lw, color=col, linestyle=ls, alpha=alpha, zorder=z))
                    else:
                        ax.add_patch(FancyArrowPatch(verts[0], verts[-1], arrowstyle="-|>", mutation_scale=9,
                                                     lw=lw, color=col, linestyle=ls, alpha=alpha, zorder=z))
                else:
                    ax.add_patch(PathPatch(MplPath(verts, codes), fc=fc, ec=ec, lw=lw, ls=ls, alpha=alpha, zorder=z))
        elif tag == "text":
            txt_val = "".join(node.itertext()).strip()
            if txt_val:
                x = ntx + _num(node.get("x")) * nsx
                y = nty + _num(node.get("y")) * nsy
                fs = max(5.0, _num(st.get("font-size"), 11.0) * 0.72 * max(nsy, 0.35))
                anchor = (st.get("text-anchor") or "start").lower()
                ha = "center" if anchor == "middle" else ("right" if anchor == "end" else "left")
                col = _color(st.get("fill"), "#1A1A1A")
                if col == "none":
                    col = "#1A1A1A"
                fw = "bold" if (st.get("font-weight") or "").lower() == "bold" else "normal"
                ax.text(x, y, txt_val, fontsize=fs, fontweight=fw, color=col, ha=ha, va="baseline", zorder=z + 50)

        for ch in node:
            _walk(ch, ntx, nty, nsx, nsy, z_acc)

    _walk(root, 0.0, 0.0, 1.0, 1.0, [0])

    out_info: dict[str, Any] = {"ok": True, "backend": "matplotlib_svg_mirror"}
    missing_glyphs: list[str] = []
    with warnings.catch_warnings(record=True) as caught_w:
        warnings.simplefilter("always")
        if png_path is not None:
            png_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(png_path, format="png", dpi=dpi)
            out_info["png"] = str(png_path)
            out_info["png_bytes"] = png_path.stat().st_size
        if pdf_path is not None:
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fig.savefig(pdf_path, format="pdf")
            except ValueError:
                plt.rcParams["pdf.fonttype"] = 3
                fig.savefig(pdf_path, format="pdf")
            out_info["pdf"] = str(pdf_path)
            out_info["pdf_bytes"] = pdf_path.stat().st_size
        for w in caught_w:
            msg = str(w.message)
            if "missing from font" in msg:
                missing_glyphs.append(msg)
    if missing_glyphs:
        out_info["missing_glyphs"] = sorted(set(missing_glyphs))[:20]
        out_info["cjk_font_warning"] = True
    plt.close(fig)
    return out_info


def render_svg_previews(
    svg_path: str | Path,
    *,
    png_path: str | Path | None = None,
    pdf_path: str | Path | None = None,
    scale: float = 2.0,
    dpi: int = 200,
) -> dict[str, Any]:
    """Render SVG into PNG and/or editable PDF cleanly without PyMuPDF/CairoSVG."""
    src = Path(svg_path)
    render_src, preserved_runs, preservation_note = _preview_svg_with_preserved_inline_space(src)
    try:
        res = _render_svg_previews_from_path(
            render_src,
            png_path=png_path,
            pdf_path=pdf_path,
            scale=scale,
            dpi=dpi,
        )
    finally:
        if render_src != src:
            render_src.unlink(missing_ok=True)
    res["preview_only_inline_space"] = {
        "changed_text_runs": preserved_runs,
        "source_svg_modified": False,
        "temporary_svg_used": render_src != src,
        "relative_resources": "same source directory",
        "fallback_note": preservation_note,
    }
    return res


def _svg_has_styled_multirun_tspan(svg_path: Path) -> bool:
    try:
        root = _safe_parse(svg_path)
    except Exception:
        return False
    for el in root.iter():
        if _local(el.tag) == "text":
            tspans = [c for c in el if _local(c.tag) == "tspan"]
            if len(tspans) >= 2 and any(
                c.get("fill") or c.get("font-weight") or c.get("font-style") or c.get("style")
                for c in tspans
            ):
                return True
    return False


def _render_svg_previews_from_path(
    svg_path: str | Path,
    *,
    png_path: str | Path | None = None,
    pdf_path: str | Path | None = None,
    scale: float = 2.0,
    dpi: int = 300,
) -> dict[str, Any]:
    """Render the provided source path using the configured local backends."""
    src = Path(svg_path)
    png_p = Path(png_path) if png_path else None
    pdf_p = Path(pdf_path) if pdf_path else None
    res: dict[str, Any] = {"ok": False, "backends": [], "requested_dpi": dpi, "degraded_warnings": []}
    rsvg_scale = max(scale, round(dpi / 96.0, 3))
    has_styled_tspan = _svg_has_styled_multirun_tspan(src)

    # PNG path: prefer rsvg-convert (respects exact DPI scale) / resvg_py, else matplotlib mirror
    if png_p is not None:
        png_p.parent.mkdir(parents=True, exist_ok=True)
        png_done = False
        rsvg_bin = shutil.which("rsvg-convert")
        if rsvg_bin:
            r = subprocess.run([rsvg_bin, "-z", str(rsvg_scale), "-o", str(png_p), str(src)], capture_output=True, text=True)
            if r.returncode == 0 and png_p.is_file() and png_p.stat().st_size > 0:
                res["png"] = str(png_p)
                res["png_bytes"] = png_p.stat().st_size
                res["backends"].append("rsvg-convert")
                png_done = True
        if not png_done:
            try:
                import resvg_py  # type: ignore
                png_bytes = resvg_py.svg_to_bytes(svg_string=src.read_text(encoding="utf-8"), zoom=int(max(1, round(rsvg_scale))))
                png_p.write_bytes(png_bytes)
                res["png"] = str(png_p)
                res["png_bytes"] = png_p.stat().st_size
                res["backends"].append("resvg_py")
                png_done = True
            except Exception:
                pass
        if not png_done:
            mpl_res = _render_via_matplotlib(src, png_p, pdf_p, dpi=dpi)
            res.update({k: v for k, v in mpl_res.items() if k != "backend"})
            res["backends"].append("matplotlib_svg_mirror")
            if has_styled_tspan:
                res["degraded_warnings"].append(
                    "MATPLOTLIB_TSPAN_DEGRADED: Matplotlib fallback may simplify inline <tspan> colors/styles; install rsvg-convert (librsvg) for full vector fidelity."
                )
            return res

    # PDF path via rsvg-convert (preferred) or Matplotlib vector mirror (fonttype=42)
    if pdf_p is not None and "pdf" not in res:
        pdf_p.parent.mkdir(parents=True, exist_ok=True)
        rsvg_bin = shutil.which("rsvg-convert")
        if rsvg_bin:
            r = subprocess.run([rsvg_bin, "-f", "pdf", "-o", str(pdf_p), str(src)], capture_output=True, text=True)
            if r.returncode == 0 and pdf_p.is_file() and pdf_p.stat().st_size > 0:
                res["pdf"] = str(pdf_p)
                res["pdf_bytes"] = pdf_p.stat().st_size
                res["backends"].append("rsvg-convert-pdf")
        if "pdf" not in res:
            mpl_res = _render_via_matplotlib(src, None, pdf_p, dpi=dpi)
            res["pdf"] = mpl_res.get("pdf")
            res["pdf_bytes"] = mpl_res.get("pdf_bytes")
            res["backends"].append("matplotlib_svg_mirror_pdf")
            if has_styled_tspan:
                res["degraded_warnings"].append(
                    "MATPLOTLIB_TSPAN_DEGRADED: Matplotlib PDF fallback may simplify inline <tspan> colors/styles; install rsvg-convert (librsvg) for full vector fidelity."
                )
            if mpl_res.get("missing_glyphs"):
                res["missing_glyphs"] = mpl_res["missing_glyphs"]
                res["cjk_font_warning"] = True

    res["ok"] = (png_p is None or (png_p.is_file() and png_p.stat().st_size > 0)) and (
        pdf_p is None or (pdf_p.is_file() and pdf_p.stat().st_size > 0)
    )
    return res


def render_pdf_to_png(pdf_path: str | Path, png_path: str | Path, *, dpi: int = 200) -> dict[str, Any]:
    """Render first page of a PDF to PNG using pdftoppm when available."""
    src = Path(pdf_path)
    dst = Path(png_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    pdftoppm_bin = shutil.which("pdftoppm")
    if not pdftoppm_bin:
        return {"ok": False, "backend": "none", "error": "pdftoppm not found"}
    prefix = dst.with_suffix("")
    r = subprocess.run(
        [pdftoppm_bin, "-png", "-r", str(dpi), "-singlefile", str(src), str(prefix)],
        capture_output=True, text=True, timeout=45,
    )
    if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
        return {"ok": True, "backend": "pdftoppm", "png": str(dst), "bytes": dst.stat().st_size}
    return {"ok": False, "backend": "pdftoppm", "error": r.stderr.strip() or "pdftoppm failed"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render SVG preview to PNG and/or PDF cleanly.")
    parser.add_argument("svg", help="Input .svg path")
    parser.add_argument("--png", default="", help="Output .png path")
    parser.add_argument("--pdf", default="", help="Output .pdf path")
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument("--dpi", type=int, default=300)
    args = parser.parse_args(argv)
    png_out = args.png or str(Path(args.svg).with_suffix(".png"))
    pdf_out = args.pdf or None
    res = render_svg_previews(args.svg, png_path=png_out, pdf_path=pdf_out, scale=args.scale, dpi=args.dpi)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
