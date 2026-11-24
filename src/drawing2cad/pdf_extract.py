"""Deterministic vector PDF extraction for Autodesk Inventor technical drawings.

Extracts:
- Part regions on multi-part sheets (via vector divider lines and part headers)
- Single-part and assembly title blocks (tabelka rysunkowa)
- Assembly Bill of Materials (BOM / wykaz części)
- Dimension values with exact PDF coordinates, multiplicity, and inferred type
- Cropped high-DPI PNG images per part for AI vision and HTML reports

Never reads ground truth or test fixtures.
"""
from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from drawing2cad.schema import (
    BomItem,
    DimensionType,
    ExtractedDimension,
    ExtractedDocument,
    PartRegion,
    TitleBlock,
)

# Autodesk Inventor AIGDT symbol font mapping
_AIGDT_MAP: dict[str, str] = {
    "P": "Ø",
    "n": "Ø",
    "Q": "□",
    "x": "x",
}

# Inventor's Tahoma exports write two CAD symbols at these WinAnsi codes.
_INVENTOR_TAHOMA_MAP: dict[str, str] = {
    "\x83": "°",
    "\x91": "Ø",
}

# drawing numbers: groups of digits separated by '.', '-' or '/' (01.01.01, 12-345, 4.10.002-1)
_DRAWING_NO = r"\d{1,6}(?:[.\-/]\d{1,6}){1,5}"
_PART_HEADER_RE = re.compile(
    rf"^(?P<name>.+?),\s*rys\.?\s*(?:nr\.?\s*)?(?P<drawing_no>{_DRAWING_NO})$",
    re.IGNORECASE,
)
_DRAWING_NO_RE = re.compile(rf"^{_DRAWING_NO}$")
_DATE_RE = re.compile(r"^(?:\d{1,2}[.\-/]\d{1,2}[.\-/](?:19|20)\d{2}|(?:19|20)\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2})$")
_SHEET_RE = re.compile(r"^\d+\s*/\s*\d+$")


def _is_drawing_no(text: str) -> bool:
    return bool(_DRAWING_NO_RE.match(text)) and not _DATE_RE.match(text) and not _SHEET_RE.match(text)


# One group of digits after a single dot is a decimal number (SolidWorks, AutoCAD: 7.85), not a
# drawing number: those have two or more separators (01.01.03) or a '-' / '/' (12-345). A drawing
# number with one dot (1120.13) is only taken as such inside a title block.
_DOT_DECIMAL_RE = re.compile(r"^\d+\.\d+$")


# a value with a negative deviation written without a space (10-0.1, 25.5-0.05): a dimension
_MINUS_DEV_RE = re.compile(r"^\d+(?:\.\d+)?-0\.\d+$")


def _is_drawing_no_in_view(text: str) -> bool:
    return bool(_DRAWING_NO_RE.match(text)) and not _DOT_DECIMAL_RE.match(text) and not _MINUS_DEV_RE.match(text)


# a number: 12, 12,5, 12.5 and the inch style without a leading zero (.250)
_N = r"(?:\d+(?:[.,]\d+)?|\.\d+)"
# multiplication sign: x, X, ×, and * (SolidWorks writes 2*M3, M30*1.5)
_X = r"[xX×*]"

# Dimension parsers (ordered from most specific to most general)
_CHAMFER_RE = re.compile(
    rf"^(?:(?P<mult>\d+)\s*{_X}\s*)?(?P<size>{_N})\s*{_X}\s*(?P<angle>{_N})\s*°$",
    re.IGNORECASE,
)
_THREAD_RE = re.compile(
    rf"^(?:(?P<mult>\d+)\s*{_X}\s*)?M(?P<diam>{_N})"
    rf"(?:\s*{_X}\s*(?P<pitch>{_N}))?(?:\s*-\s*(?P<cls>\d[a-hA-H]{{1,2}}))?"
    r"(?:\s*(?P<hand>lewy|LH|prawy|RH))?$",
    re.IGNORECASE,
)
_FIT = r"[A-Za-z]{1,2}\d{1,2}(?:\s*/\s*[A-Za-z]{1,2}\d{1,2})?"
_DEV = rf"±\s*{_N}|[+-]\s*{_N}(?:\s*/?\s*[+-]?\s*{_N})?"
# tolerance / fit written after the nominal value; a fit may carry its limit deviations (g6 -0,025)
_TOL_SUFFIX_RE = re.compile(
    r"^(?P<main>.*?\d(?:[.,]\d+)?°?)\s*(?P<tol>"
    rf"{_DEV}"
    rf"|{_FIT}(?:\s*(?:{_DEV}))?"
    r"|max|min"
    r")$",
    re.IGNORECASE,
)
# a tolerance written as its own text (stacked limits); 0 and 0,00 are a zero limit
_TOL_ALONE_RE = re.compile(rf"^(?:±\s*{_N}|[+-]\s*{_N}|0(?:[.,]0+)?)$")
_MULT_PREFIX_RE = re.compile(r"^(?P<n>\d+)\s*(?:[xX×*]|otw\.?|otwory|otworów)\s+(?P<rest>.+)$", re.IGNORECASE)
_MULT_SUFFIX_RE = re.compile(r"^(?P<rest>.+?)\s*\(\s*(?P<n>\d+)\s*[x×]\s*\)$|^(?P<rest2>.+?)\s+(?P<n2>\d+)\s*[x×]$")
# looks like a dimension: short, has a digit, no long words (notes, headers, table text)
_DIMLIKE_RE = re.compile(r"\d")
_DIAMETER_RE = re.compile(rf"^(?:(?P<mult>\d+)\s*{_X}\s*)?Ø\s*(?P<val>{_N})$")
_RADIUS_RE = re.compile(rf"^(?:(?P<mult>\d+)\s*{_X}\s*)?R\s*(?P<val>{_N})$")
_SQUARE_RE = re.compile(rf"^(?:(?P<mult>\d+)\s*{_X}\s*)?□\s*(?P<val>{_N})$")
_ANGLE_RE = re.compile(rf"^(?:(?P<mult>\d+)\s*{_X}\s*)?(?P<val>{_N})\s*°$")
_NUMBER_RE = re.compile(rf"^(?P<val>{_N})$")
# a view label with its own scale (SCALE 1 : 2): not a dimension, nothing to check
_VIEW_SCALE_RE = re.compile(r"^(?:SCALE|SKALA|MASSTAB|MAßSTAB)\s*\d+(?:[.,]\d+)?\s*:\s*\d+(?:[.,]\d+)?$", re.IGNORECASE)
# a web address (company stamp next to the title block): its digits are not a dimension
_URL_RE = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)


def decode_inventor_text(text: str, font: str = "") -> str:
    """Decode Autodesk Inventor PDF font quirks (AIGDT symbols & Tahoma Polish diacritics)."""
    if font.upper().startswith("AIGDT"):
        return "".join(_AIGDT_MAP.get(ch, ch) for ch in text)
    return "".join(_INVENTOR_TAHOMA_MAP.get(ch, ch) for ch in text)


@dataclass
class _RawSpan:
    text: str
    raw_text: str
    font: str
    size: float
    bbox: tuple[float, float, float, float]
    direction: tuple[float, float]
    start: tuple[float, float] | None = None  # baseline origin of the first character

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2.0

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2.0


@dataclass
class _TextCluster:
    raw_text: str
    decoded_text: str
    bbox: tuple[float, float, float, float]
    direction: tuple[float, float]
    size: float
    type_uncertain: bool = False  # an unreadable symbol drawn as curves stands before the text
    start: tuple[float, float] | None = None  # baseline origin of the first character

    @property
    def center(self) -> tuple[float, float]:
        return ((self.bbox[0] + self.bbox[2]) / 2.0, (self.bbox[1] + self.bbox[3]) / 2.0)


def _font_key(name: str) -> str:
    """'ADSINV+Tahoma' -> 'tahoma' (subset prefix removed)."""
    return name.split("+", 1)[-1].lower()


def _glyph_maps(page: pymupdf.Page) -> dict[str, list[dict[int, str]]]:
    """glyph id -> character for every embedded composite (Type0) TrueType font, by font name.

    Inventor writes some characters (Polish letters) through an Identity-H font without a
    usable ToUnicode entry; PDF readers then return the glyph id instead of the character.
    The embedded font program still has its own cmap, which gives the real character."""
    maps: dict[str, list[dict[int, str]]] = {}
    try:
        from fontTools.ttLib import TTFont
    except ImportError:  # pragma: no cover - fontTools ships with the CAD stack
        return maps
    doc = page.parent
    for xref, ext, ftype, basefont, *_ in page.get_fonts():
        if ftype != "Type0" or ext not in ("ttf", "otf", "cff"):
            continue
        try:
            buf = doc.extract_font(xref)[3]
            font = TTFont(io.BytesIO(buf))
            order = font.getGlyphOrder()
            by_name: dict[str, str] = {}
            for code, name in (font.getBestCmap() or {}).items():
                by_name.setdefault(name, chr(code))
            gmap = {gid: by_name[name] for gid, name in enumerate(order) if name in by_name}
            maps.setdefault(_font_key(basefont), []).append(gmap)
        except Exception:
            continue
    return maps


def _unmapped_glyphs(page: pymupdf.Page, maps: dict[str, list[dict[int, str]]]) -> dict[tuple[float, float], str]:
    """Characters the PDF could not map to Unicode, keyed by their origin, decoded via glyph ids
    of the font the character was drawn with. Ambiguous glyphs stay undecoded (visible as '?')."""
    out: dict[tuple[float, float], str] = {}
    if not maps:
        return out
    for tr in page.get_texttrace():
        fmaps = maps.get(_font_key(tr.get("font", "")), [])
        for ucs, gid, origin, _bbox in tr["chars"]:
            if ucs != 0xFFFD:
                continue
            options = {m[gid] for m in fmaps if gid in m}
            if len(options) == 1:
                out[(round(origin[0], 1), round(origin[1], 1))] = options.pop()
    return out


def _extract_spans(page: pymupdf.Page) -> list[_RawSpan]:
    spans: list[_RawSpan] = []
    fixes = _unmapped_glyphs(page, _glyph_maps(page))
    text_dict = page.get_text("rawdict")
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            dx, dy = line.get("dir", (1.0, 0.0))
            norm = math.hypot(dx, dy) or 1.0
            direction = (dx / norm, dy / norm)
            for span in line.get("spans", []):
                chars = span.get("chars", [])
                raw = "".join(ch["c"] for ch in chars)
                if not raw.strip():
                    continue
                font = span.get("font", "")
                if font.upper().startswith("AIGDT"):
                    fixed = raw  # symbol font: its letters are the symbol codes (P = Ø, Q = □)
                else:
                    fixed = "".join(
                        fixes.get((round(ch["origin"][0], 1), round(ch["origin"][1], 1)), ch["c"]) for ch in chars
                    )
                decoded = decode_inventor_text(fixed, font)
                x0, y0, x1, y1 = span["bbox"]
                spans.append(
                    _RawSpan(
                        text=decoded,
                        raw_text=raw,
                        font=font,
                        size=float(span.get("size", 10.0)),
                        bbox=(float(x0), float(y0), float(x1), float(y1)),
                        direction=direction,
                        start=tuple(map(float, next(ch for ch in chars if ch["c"].strip())["origin"])),
                    )
                )
    return spans


def _project_interval(
    bbox: tuple[float, float, float, float], axis: tuple[float, float]
) -> tuple[float, float]:
    """Project all 4 corners of an axis-aligned bbox onto unit vector `axis`."""
    x0, y0, x1, y1 = bbox
    ax, ay = axis
    vals = (
        x0 * ax + y0 * ay,
        x1 * ax + y0 * ay,
        x0 * ax + y1 * ay,
        x1 * ax + y1 * ay,
    )
    return (min(vals), max(vals))


def _cluster_spans(spans: list[_RawSpan], max_gap: float = 10.0, max_perp: float = 8.0) -> list[_TextCluster]:
    """Group collinear, adjacent spans along their baseline vector `dir`.

    Fixes PyMuPDF's reversal of 90°-rotated and slanted Inventor text (e.g.
    `10 M lewy` -> `M10 lewy`, `P 10 2x` -> `2xØ10`, `7 Q` -> `□7`) while
    keeping spatially separated dimensions (`150` and `150`, `15` and `21`) distinct.
    """
    if not spans:
        return []

    n = len(spans)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        si = spans[i]
        dx, dy = si.direction
        px, py = -dy, dx
        pi_perp = si.cx * px + si.cy * py
        li0, li1 = _project_interval(si.bbox, si.direction)
        for j in range(i + 1, n):
            sj = spans[j]
            dot = dx * sj.direction[0] + dy * sj.direction[1]
            if dot < 0.985:
                continue
            pj_perp = sj.cx * px + sj.cy * py
            if abs(pi_perp - pj_perp) > max_perp:
                continue
            lj0, lj1 = _project_interval(sj.bbox, si.direction)
            gap = max(li0 - lj1, lj0 - li1)
            # For slanted boxes the AABB projection is slightly wider than the actual glyph box;
            # keep threshold tight enough to separate distinct dimensions but merge multi-span tokens.
            allowed_gap = max_gap if abs(dx) > 0.99 or abs(dy) > 0.99 else 6.0
            if gap <= allowed_gap:
                union(i, j)

    groups: dict[int, list[_RawSpan]] = {}
    for i, sp in enumerate(spans):
        groups.setdefault(find(i), []).append(sp)

    clusters: list[_TextCluster] = []
    for group in groups.values():
        d = group[0].direction
        # Sort spans strictly along the reading direction vector!
        group.sort(key=lambda s: s.cx * d[0] + s.cy * d[1])

        decoded_parts: list[str] = []
        raw_parts: list[str] = []
        for idx, sp in enumerate(group):
            if idx > 0:
                prev = group[idx - 1]
                _, prev_end = _project_interval(prev.bbox, d)
                curr_start, _ = _project_interval(sp.bbox, d)
                inter_gap = curr_start - prev_end
                # Preserve word spaces when there's a clear whitespace gap (> 2.5pt)
                # and neither side already has a space.
                if (
                    inter_gap > 2.5
                    and not decoded_parts[-1].endswith(" ")
                    and not sp.text.startswith(" ")
                ):
                    decoded_parts.append(" ")
                    raw_parts.append(" ")
            decoded_parts.append(sp.text)
            raw_parts.append(sp.raw_text)

        decoded = re.sub(r"\s+", " ", "".join(decoded_parts)).strip()
        raw = re.sub(r"\s+", " ", "".join(raw_parts)).strip()
        x0 = min(s.bbox[0] for s in group)
        y0 = min(s.bbox[1] for s in group)
        x1 = max(s.bbox[2] for s in group)
        y1 = max(s.bbox[3] for s in group)
        avg_size = sum(s.size for s in group) / len(group)
        clusters.append(
            _TextCluster(
                raw_text=raw,
                decoded_text=decoded,
                bbox=(round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)),
                direction=(round(d[0], 3), round(d[1], 3)),
                size=avg_size,
                start=group[0].start,
            )
        )

    # Stable reading order: top-to-bottom, left-to-right
    clusters.sort(key=lambda c: (round(c.bbox[1] / 5.0), c.bbox[0]))
    return clusters


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def parse_dimension_token(
    cluster: _TextCluster, dim_id: str
) -> ExtractedDimension | None:
    """Parse a clustered text string into a structured ExtractedDimension if it is a dimension."""
    txt = cluster.decoded_text.strip()
    if not txt:
        return None
    # Reject dates, scales, sheet numbers, drawing numbers, labels
    if _is_drawing_no_in_view(txt) or _PART_HEADER_RE.match(txt):
        return None
    if ":" in txt or _SHEET_RE.match(txt) or _DATE_RE.match(txt):  # scales, sheet numbers, dates
        return None

    full_txt = txt
    reference = False
    if txt.startswith("(") and txt.endswith(")") and _DIMLIKE_RE.search(txt):
        reference, txt = True, txt[1:-1].strip()  # reference (auxiliary) dimension
    extra_mult = None
    if m := _MULT_PREFIX_RE.match(txt):
        if not re.match(r"^\d+\s*[xX×*]\s*\d+(?:[.,]\d+)?\s*°", txt):  # keep chamfers like 1x45°
            inner = m.group("rest").strip()
            if re.match(r"^(?:Ø|R|□|M)", inner):
                extra_mult, txt = int(m.group("n")), inner
    elif m := _MULT_SUFFIX_RE.match(txt):
        extra_mult = int(m.group("n") or m.group("n2"))
        txt = (m.group("rest") or m.group("rest2")).strip()

    def _make(
        dim_type: DimensionType,
        value: float | None,
        mult: int = 1,
        secondary: float | None = None,
        thread_spec: str | None = None,
        tolerance: str | None = None,
    ) -> ExtractedDimension:
        return ExtractedDimension(
            id=dim_id,
            raw_text=cluster.raw_text,
            decoded_text=full_txt,
            dim_type=dim_type,
            value=value,
            secondary_value=secondary,
            multiplicity=extra_mult or mult,
            thread_spec=thread_spec,
            bbox=cluster.bbox,
            direction=cluster.direction,
            tolerance=tolerance,
            reference=reference,
            type_uncertain=cluster.type_uncertain,
        )

    parsed = _parse_core(txt, _make)
    if parsed is None and (m := _TOL_SUFFIX_RE.match(txt)):
        core = _parse_core(m.group("main").strip(), _make)
        if core is not None:
            parsed = core.model_copy(update={"tolerance": _normalize_tol(m.group("tol"))})
    if parsed is not None and parsed.value is not None and parsed.value <= 0:
        return None  # Ø0, R0 ... are tolerance frame entries, never a feature size
    return parsed


def _normalize_tol(tol: str) -> str:
    """'+0,1 / -0,2' -> '+0,1/-0,2'; a fit keeps one space before its deviations: 'g6 -0,025'."""
    tol = re.sub(r"\s+", "", tol)
    return re.sub(r"^([A-Za-z]{1,2}\d{1,2}(?:/[A-Za-z]{1,2}\d{1,2})?)(?=[±+-])", r"\1 ", tol)


def _parse_core(txt: str, _make) -> ExtractedDimension | None:
    """The nominal dimension forms (no tolerance)."""

    if m := _CHAMFER_RE.match(txt):
        mult = int(m.group("mult") or 1)
        return _make("fazka", _to_float(m.group("size")), mult=mult, secondary=_to_float(m.group("angle")))

    if m := _THREAD_RE.match(txt):
        mult = int(m.group("mult") or 1)
        diam = _to_float(m.group("diam"))
        hand = (m.group("hand") or "").strip().lower()
        spec = f"M{diam:g}"
        if m.group("pitch"):
            spec += f"x{_to_float(m.group('pitch')):g}"
        if m.group("cls"):
            spec += f"-{m.group('cls')}"
        if hand in {"lewy", "lh"}:
            spec += " LH"
        return _make("gwint", diam, mult=mult, thread_spec=spec)

    if m := _DIAMETER_RE.match(txt):
        mult = int(m.group("mult") or 1)
        return _make("srednica", _to_float(m.group("val")), mult=mult)

    if m := _RADIUS_RE.match(txt):
        mult = int(m.group("mult") or 1)
        return _make("promien", _to_float(m.group("val")), mult=mult)

    if m := _SQUARE_RE.match(txt):
        mult = int(m.group("mult") or 1)
        return _make("kwadrat", _to_float(m.group("val")), mult=mult)

    if m := _ANGLE_RE.match(txt):
        mult = int(m.group("mult") or 1)
        return _make("kat", _to_float(m.group("val")), mult=mult)

    if m := _NUMBER_RE.match(txt):
        val = _to_float(m.group("val"))
        return _make("dlugosc", val, mult=1) if val > 0 else None  # a lone 0 is a tolerance limit

    if m := re.match(r"^[gt]\s*=\s*(?P<val>\d+(?:[.,]\d+)?)$", txt, re.IGNORECASE):
        return _make("dlugosc", _to_float(m.group("val")), mult=1)  # sheet / plate thickness

    return None


def _rect_gap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dy)


def _upness(c: _TextCluster, direction: tuple[float, float]) -> float:
    """How high the text sits in the reading frame of `direction` (larger = higher line)."""
    dx, dy = direction
    cx, cy = c.center
    return cx * dy - cy * dx


def _stack_tol(own: str | None, own_up: float, extra: list[tuple[float, str]]) -> str:
    """Join the limit deviations written one above the other: upper first ('+0,01/-0,01').
    A fit stays in front of its deviations ('g6 -0,009/-0,025')."""
    fit, dev = None, own
    if own and (m := re.match(r"^([A-Za-z]{1,2}\d{1,2}(?:/[A-Za-z]{1,2}\d{1,2})?)(?:\s+(.+))?$", own)):
        fit, dev = m.group(1), m.group(2)
    pieces = ([(own_up, dev)] if dev else []) + list(extra)
    devs = "/".join(t for _, t in sorted(pieces, key=lambda p: -p[0]))
    return f"{fit} {devs}" if fit else devs


def _deviation_count(tol: str | None) -> int:
    """Limit deviations a tolerance already has: '±0,1' and 'max'/'min' count as complete (2)."""
    if not tol:
        return 0
    if "±" in tol or re.search(r"max|min", tol, re.IGNORECASE):
        return 2
    dev = re.sub(r"^[A-Za-z]{1,2}\d{1,2}(?:/[A-Za-z]{1,2}\d{1,2})?\s*", "", tol)  # fit letters aside
    return min(2, len(re.findall(r"\d+(?:[.,]\d+)?|[.,]\d+", dev)))


def _is_zero_limit(text: str) -> bool:
    return bool(re.fullmatch(r"\s*0(?:[.,]0+)?\s*", text))


def _parse_region(clusters, skip=lambda c: False) -> tuple[list[ExtractedDimension], list[str], list[str]]:
    """Dimensions of one drawing area; texts that look like dimensions but were not parsed; other
    texts with digits (notes, labels) that are not dimensions, so that nothing disappears silently.
    Stand-alone tolerance texts (stacked limits) are attached to the nearest dimension, or to the
    nearest unrecognized text so that the whole unread dimension is reported in one piece."""
    dims: list[ExtractedDimension] = []
    dim_clusters: list[_TextCluster] = []
    loose_tols: list[_TextCluster] = []
    unrecognized: list[_TextCluster] = []
    others: list[_TextCluster] = []
    for c in clusters:
        if skip(c):
            continue
        t = c.decoded_text.strip()
        dim = parse_dimension_token(c, f"D{len(dims) + 1}")
        if dim is not None:
            dims.append(dim)
            dim_clusters.append(c)
        elif _TOL_ALONE_RE.match(t):
            loose_tols.append(c)
        elif _looks_like_dimension(t):
            unrecognized.append(c)
        elif _DIMLIKE_RE.search(t) and not _VIEW_SCALE_RE.match(t) and not _URL_RE.match(t):
            others.append(c)

    targets = dim_clusters + unrecognized
    # how many limit deviations each target still lacks: '±0,1' or '+0,1/-0,2' is complete
    room: dict[int, int] = {}
    for c, dim in zip(dim_clusters, dims):
        room[id(c)] = 2 - _deviation_count(dim.tolerance)
    for c in unrecognized:
        m = _TOL_SUFFIX_RE.match(c.decoded_text)
        room[id(c)] = 2 - (_deviation_count(_normalize_tol(m.group("tol"))) if m else 0)
    pieces: dict[int, list[tuple[float, str]]] = {}
    attached: dict[int, list[_TextCluster]] = {}
    leftover: list[_TextCluster] = []
    for t in loose_tols:
        best = None
        for c in targets:
            if room[id(c)] <= 0:
                continue  # its tolerance is already complete
            if abs(t.direction[0] * c.direction[0] + t.direction[1] * c.direction[1]) < 0.985:
                continue  # limits are written along the nominal value
            if abs((t.center[0] - c.center[0]) * -c.direction[1] + (t.center[1] - c.center[1]) * c.direction[0]) \
                    > 1.2 * max(t.size, c.size):
                continue  # stacked limits stand next to the value, at most a line above or below it
            gap, cd = _rect_gap(t.bbox, c.bbox), math.dist(t.center, c.center)
            # limits are written after the start of the nominal value; side by side texts that start
            # level with each other (ordinate values 0, 1.5 ...) are separate dimensions
            behind = _project_interval(t.bbox, c.direction)[0] - _project_interval(c.bbox, c.direction)[0]
            if behind < 0.4 * c.size:
                continue
            if (gap <= max(6.0, 0.8 * max(t.size, c.size)) or cd <= 25) and (best is None or (gap, cd) < best[0]):
                best = ((gap, cd), c)
        if best is None:
            leftover.append(t)
            continue
        target = best[1]
        room[id(target)] -= 1
        attached.setdefault(id(target), []).append(t)
    for c in targets:
        got = attached.get(id(c), [])
        own = room[id(c)] + len(got) < 2  # the target carries a deviation of its own
        if got and not own and all(_is_zero_limit(t.decoded_text) for t in got):
            # a lone 0 (an ordinate value) is a limit only as one half of a stacked pair
            leftover.extend(got)
            continue
        for t in got:
            pieces.setdefault(id(c), []).append((_upness(t, c.direction), re.sub(r"\s+", "", t.decoded_text)))

    for i, c in enumerate(dim_clusters):
        if extra := pieces.get(id(c)):
            dims[i] = dims[i].model_copy(update={"tolerance": _stack_tol(dims[i].tolerance, _upness(c, c.direction), extra)})
    unknown: list[str] = []
    for c in unrecognized:
        text = c.decoded_text
        if extra := pieces.get(id(c)):
            if m := _TOL_SUFFIX_RE.match(text):
                text = f"{m.group('main').strip()} {_stack_tol(_normalize_tol(m.group('tol')), _upness(c, c.direction), extra)}"
            else:
                text = _stack_tol(None, 0.0, [(_upness(c, c.direction), text), *extra])
        unknown.append(text)
    unknown.extend(t.decoded_text for t in leftover)
    return dims, unknown, [o.decoded_text for o in others]


def _parse_region_dimensions(clusters, skip=lambda c: False) -> tuple[list[ExtractedDimension], list[str]]:
    """Dimensions of one drawing area, plus texts that look like dimensions but were not parsed."""
    dims, unknown, _ = _parse_region(clusters, skip)
    return dims, unknown


def _looks_like_dimension(text: str) -> bool:
    t = text.strip()
    if not _DIMLIKE_RE.search(t) or len(t) > 24 or _PART_HEADER_RE.match(t) or _is_drawing_no(t):
        return False
    if ":" in t or _SHEET_RE.match(t) or _DATE_RE.match(t):  # scale, sheet, date
        return False
    if re.match(r"^(?:Ra|Rz)\s*\d", t) or re.match(r"^[A-Z]{1,3}\d{3,4}[A-Z0-9+]*$", t):  # roughness, material
        return False
    words = [w for w in re.split(r"\s+", t) if w]
    return len(words) <= 3 and not any(len(re.sub(r"[^A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]", "", w)) > 5 for w in words)


def _detect_page_regions(
    page: pymupdf.Page,
) -> list[tuple[float, float, float, float]]:
    """Detect rectangular part cells on a page using full-span vector divider lines."""
    pw, ph = float(page.rect.width), float(page.rect.height)
    h_ys: list[float] = []
    v_lines: list[tuple[float, float, float]] = []

    for d in page.get_drawings():
        r = d["rect"]
        # Full-width horizontal divider (inside page margins, outside title block at bottom)
        if r.width >= 0.85 * pw and r.height < 2.0 and 40.0 < r.y0 < ph - 120.0:
            h_ys.append(float((r.y0 + r.y1) / 2.0))
        elif r.height >= 120.0 and r.width < 2.0 and 40.0 < r.x0 < pw - 40.0:
            v_lines.append((float((r.x0 + r.x1) / 2.0), float(r.y0), float(r.y1)))

    # Deduplicate nearby horizontal dividers
    h_ys.sort()
    dedup_h: list[float] = []
    for y in h_ys:
        if not dedup_h or abs(y - dedup_h[-1]) > 10.0:
            dedup_h.append(y)

    if not dedup_h:
        return [(0.0, 0.0, pw, ph)]

    y_bounds = [0.0, *dedup_h, ph]
    regions: list[tuple[float, float, float, float]] = []
    for y0, y1 in zip(y_bounds[:-1], y_bounds[1:]):
        band_h = y1 - y0
        if band_h < 40.0:
            continue
        # Check for vertical dividers spanning at least 80% of this horizontal band
        band_xs: list[float] = []
        for vx, vy0, vy1 in v_lines:
            overlap = min(y1, vy1) - max(y0, vy0)
            if overlap >= 0.80 * band_h:
                band_xs.append(vx)
        band_xs.sort()
        dedup_x: list[float] = []
        for x in band_xs:
            if not dedup_x or abs(x - dedup_x[-1]) > 10.0:
                dedup_x.append(x)
        x_bounds = [0.0, *dedup_x, pw]
        for x0, x1 in zip(x_bounds[:-1], x_bounds[1:]):
            regions.append((round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)))

    return regions


def _span_in_region(span: _RawSpan, region: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = region
    return x0 <= span.cx <= x1 and y0 <= span.cy <= y1


def _title_block_rect(page: pymupdf.Page) -> tuple[float, float, float, float] | None:
    """Title block = visible-width lines attached to the sheet frame (views never touch it)."""
    from drawing2cad.drawing_compare import page_styles

    drawings = page.get_drawings()
    styles = page_styles(drawings, float(page.rect.width), float(page.rect.height))
    if styles is None or styles.frame is None:
        return None
    fx0, fy0, fx1, fy1 = styles.frame
    boxes = []
    for d in drawings:
        w = d.get("width") or 0.0
        if not (styles.visible[0] <= w <= styles.visible[1]):
            continue
        r = d["rect"]
        if abs(r.y1 - fy1) < 3 or abs(r.x1 - fx1) < 3:
            boxes.append((r.x0, r.y0, r.x1, r.y1))
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    if fy1 - y0 > 0.5 * (fy1 - fy0):
        return None
    return (x0, y0, fx1, fy1)


def _parse_title_block(
    clusters: list[_TextCluster], page_height: float, rect: tuple[float, float, float, float] | None = None
) -> tuple[TitleBlock | None, float]:
    """Parse the title block (tabelka rysunkowa). Positions are relative to the block itself.

    Returns (TitleBlock | None, title_block_top_y).
    """
    if rect is not None:
        tx0, ty0, tx1, ty1 = rect
        tb_clusters = [
            c for c in clusters
            if tx0 - 1 <= (c.bbox[0] + c.bbox[2]) / 2 <= tx1 + 1 and ty0 - 1 <= (c.bbox[1] + c.bbox[3]) / 2 <= ty1 + 1
        ]
    else:
        ty0 = page_height * 0.84
        tb_clusters = [c for c in clusters if c.bbox[1] >= ty0]
        if tb_clusters:
            tx0 = min(c.bbox[0] for c in tb_clusters)
            tx1 = max(c.bbox[2] for c in tb_clusters)
            ty1 = max(c.bbox[3] for c in tb_clusters)
    if not tb_clusters:
        return None, page_height

    texts = [c.decoded_text for c in tb_clusters]
    if not any(any(k in t for k in ("Zaprojektowany", "Skala", "Arkusz", "Wydanie")) for t in texts):
        return None, page_height

    tw, th = max(tx1 - tx0, 1.0), max(ty1 - ty0, 1.0)

    def rel_x(c) -> float:
        return (c.bbox[0] - tx0) / tw

    def rel_y(c) -> float:
        return (c.bbox[1] - ty0) / th

    tb = TitleBlock()
    tb_top = (ty0 if rect is not None else min(c.bbox[1] for c in tb_clusters)) - 4.0

    for c in tb_clusters:
        if _is_drawing_no(c.decoded_text):
            tb.nr_rysunku = c.decoded_text
            break
    for c in tb_clusters:
        if re.match(r"^\d+(?:[.,]\d+)?\s*:\s*\d+(?:[.,]\d+)?$", c.decoded_text):
            tb.skala = c.decoded_text.replace(" ", "")
            break
    for c in tb_clusters:
        if _SHEET_RE.match(c.decoded_text):
            tb.arkusz = c.decoded_text
            break

    # part / assembly name: the largest text in the right part of the block
    name_cands = [
        c for c in tb_clusters
        if rel_x(c) > 0.4
        and not _is_drawing_no(c.decoded_text)
        and not re.match(r"^\d+\s*[:/]\s*\d+$", c.decoded_text)
        and not any(k in c.decoded_text for k in ("Skala", "Data", "Wydanie", "Arkusz", "przez"))
    ]
    if name_cands:
        tb.nazwa = max(name_cands, key=lambda c: c.size).decoded_text

    zapr = [c for c in tb_clusters if "Zaprojektowany" in c.decoded_text]
    if zapr:
        z = zapr[0]
        cands = [c for c in tb_clusters if c is not z and abs(c.bbox[0] - z.bbox[0]) < 0.1 * tw
                 and z.bbox[3] <= c.bbox[1] <= z.bbox[3] + 0.25 * th]
        if cands:
            tb.autor = min(cands, key=lambda c: c.bbox[1]).decoded_text

    # institution: larger multi-line text in the lower-left part of the block
    big = max((c.size for c in tb_clusters), default=0.0)
    inst = [c for c in tb_clusters if rel_x(c) < 0.45 and rel_y(c) > 0.3 and c.size >= 0.75 * big
            and "przez" not in c.decoded_text]
    if inst:
        tb.instytucja = " ".join(c.decoded_text for c in sorted(inst, key=lambda c: c.bbox[1]))

    return tb, tb_top


def _parse_bom(page: pymupdf.Page, spans: list[_RawSpan], table_bottom: float) -> tuple[list[BomItem], float]:
    """Parse the BOM table (wykaz części) on assembly drawings.

    Rows come from the table's horizontal lines, columns from its vertical lines (or, without
    them, from the midpoints between the header labels). Returns (bom_items, bom_top_y).
    """
    all_clusters = _cluster_spans(spans, max_gap=8.0, max_perp=6.0)
    header_keys = {"poz": ("ELEMENT", "POZ", "LP"), "ilosc": ("ILO",), "nazwa": ("NAZWA",), "nr": ("RYSUNKU", "NR")}
    header: dict[str, _TextCluster] = {}
    for c in all_clusters:
        u = c.decoded_text.upper()
        for key, words in header_keys.items():
            if key not in header and any(w in u for w in words):
                if key == "nr" and "NAZWA" in u:
                    continue
                header[key] = c
                break
    if len(header) < 3 or "nazwa" not in header:
        return [], float(page.rect.height)

    header_y0 = min(c.bbox[1] for c in header.values())
    header_y1 = max(c.bbox[3] for c in header.values())
    table_x0 = min(c.bbox[0] for c in header.values()) - 60
    table_x1 = max(c.bbox[2] for c in header.values()) + 60

    drawings = page.get_drawings()
    row_ys: list[float] = []
    col_xs: list[float] = []
    for d in drawings:
        r = d["rect"]
        if r.height < 2.0 and r.width > 0.5 * (table_x1 - table_x0) and header_y1 - 2.0 <= r.y0 <= table_bottom + 2:
            row_ys.append(float((r.y0 + r.y1) / 2.0))
        if r.width < 2.0 and r.y0 <= header_y0 + 2 and r.y1 >= header_y1 - 2 and table_x0 <= r.x0 <= table_x1:
            col_xs.append(float((r.x0 + r.x1) / 2.0))
    row_ys.sort()
    rows: list[float] = []
    for y in row_ys:
        if not rows or abs(y - rows[-1]) > 5.0:
            rows.append(y)
    if rows and table_bottom - rows[-1] > 5.0:
        rows.append(table_bottom)  # the last row sits on the title block

    order = sorted(header, key=lambda k: (header[k].bbox[0] + header[k].bbox[2]) / 2)
    centers = [(header[k].bbox[0] + header[k].bbox[2]) / 2 for k in order]
    col_xs = sorted({round(x, 1) for x in col_xs})
    inner = [x for x in col_xs if centers[0] < x < centers[-1]]
    if len(inner) >= len(order) - 1:
        bounds = [max((x for x in inner if x < c2), default=(c1 + c2) / 2) for c1, c2 in zip(centers, centers[1:])]
    else:
        bounds = [(c1 + c2) / 2 for c1, c2 in zip(centers, centers[1:])]

    def column_of(x: float) -> str:
        for key, b in zip(order, bounds):
            if x < b:
                return key
        return order[-1]

    bom: list[BomItem] = []
    for r_y0, r_y1 in zip(rows[:-1], rows[1:]):
        row_spans = [s for s in spans if r_y0 <= s.cy <= r_y1 and table_x0 <= s.cx <= table_x1]
        if not row_spans:
            continue
        cols: dict[str, list[str]] = {k: [] for k in header_keys}
        for c in sorted(_cluster_spans(row_spans, max_gap=8.0, max_perp=6.0), key=lambda c: c.bbox[0]):
            cols[column_of((c.bbox[0] + c.bbox[2]) / 2.0)].append(c.decoded_text)
        poz_str = "".join(cols["poz"]).strip()
        ilosc_str = "".join(cols["ilosc"]).strip()
        nazwa = " ".join(cols["nazwa"]).strip()
        nr = "".join(cols["nr"]).strip() or None
        if poz_str.isdigit() and ilosc_str.isdigit() and nazwa:
            bom.append(BomItem(poz=int(poz_str), ilosc=int(ilosc_str), nazwa=nazwa,
                               nr_rysunku=nr, znormalizowana=(nr is None)))

    bom.sort(key=lambda b: b.poz)
    return bom, header_y0 - 4.0


def _balloon_circles(page: pymupdf.Page) -> list[tuple[float, float, float]]:
    """Small closed circles (item balloons) as (cx, cy, r) in PDF points."""
    out = []
    for d in page.get_drawings():
        items = d["items"]
        if len(items) >= 4 and all(it[0] == "c" for it in items):
            r = d["rect"]
            if 8 <= r.width <= 40 and abs(r.width - r.height) < 1.0:
                out.append(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2, r.width / 2))
    return out


# --- drawings from other programs than Inventor ------------------------------------------------

_ZONE_LABEL_RE = re.compile(r"^(?:\d{1,2}|[A-Za-z])$")


def _frame_zone_labels(clusters: list[_TextCluster], pw: float, ph: float) -> set[int]:
    """Zone labels of the sheet frame (1..8 along the top/bottom edge, A..F along the sides): single
    numbers or letters in the margin strip of an edge, on one line parallel to that edge, at least
    four of them, consecutive without repeats and evenly spaced. Anything else stays ordinary text.
    Returns ids of those clusters."""
    margin = min(max(0.055 * max(pw, ph), 30.0), 70.0)
    edges: dict[str, list[_TextCluster]] = {}
    for c in clusters:
        if not _ZONE_LABEL_RE.match(c.decoded_text.strip()):
            continue
        cx, cy = c.center
        dist = {"top": cy, "bottom": ph - cy, "left": cx, "right": pw - cx}
        edge = min(dist, key=dist.get)
        if dist[edge] <= margin:
            edges.setdefault(edge, []).append(c)
    out: set[int] = set()
    for edge, group in edges.items():
        across = 1 if edge in ("top", "bottom") else 0  # the coordinate shared by one row of labels
        for kind in ("digit", "alpha"):
            members = [c for c in group if c.decoded_text.strip().isdigit() == (kind == "digit")]
            # one row: labels whose line coordinate agrees within half a text height
            rows: list[list[_TextCluster]] = []
            for c in sorted(members, key=lambda c: c.center[across]):
                if rows and abs(c.center[across] - rows[-1][0].center[across]) <= max(0.5 * c.size, 2.0):
                    rows[-1].append(c)
                else:
                    rows.append([c])
            for row in rows:
                if len(row) < 4:
                    continue
                row.sort(key=lambda c: c.center[1 - across])
                keys = [int(c.decoded_text) if kind == "digit" else ord(c.decoded_text.strip().lower()) for c in row]
                steps = {b - a for a, b in zip(keys, keys[1:])}
                if steps not in ({1}, {-1}):
                    continue  # not consecutive, or a repeated label
                gaps = [b.center[1 - across] - a.center[1 - across] for a, b in zip(row, row[1:])]
                if min(gaps) <= 0 or max(gaps) > 1.35 * min(gaps):
                    continue
                out.update(id(c) for c in row)
    return out


# field labels of title blocks in English-language programs (SolidWorks, AutoCAD, Creo...)
_TB_LABEL_RE = re.compile(
    r"\b(?:title|dwg\.?\s*no|drawing\s*(?:no|number)|part\s*(?:name|no|number)|material|weight|mass|scale|sheet|"
    r"size|drawn|checked|approved|approvals|designed|issued|rev(?:ision)?|date|finish|tolerances?|projection|"
    r"units?|doc\.?\s*type|description|machine)\b",
    re.IGNORECASE,
)
_TB_NUMBER_LABEL_RE = re.compile(r"\b(?:dwg\.?\s*no|drawing\s*(?:no|number)|part\s*(?:no|number))\b", re.IGNORECASE)
_TB_NAME_LABEL_RE = re.compile(r"\b(?:title|part\s*name|description|name)\b", re.IGNORECASE)


def _segments(drawings) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """Horizontal (y, x0, x1) and vertical (x, y0, y1) straight line segments of the page."""
    hs: list[tuple[float, float, float]] = []
    vs: list[tuple[float, float, float]] = []
    for d in drawings:
        for it in d["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                pairs = [(a, b)]
            elif it[0] == "re":
                r = it[1]
                pairs = [(r.tl, r.tr), (r.bl, r.br), (r.tl, r.bl), (r.tr, r.br)]
            else:
                continue
            for a, b in pairs:
                if abs(a.y - b.y) < 0.5 and abs(a.x - b.x) > 5:
                    hs.append(((a.y + b.y) / 2, min(a.x, b.x), max(a.x, b.x)))
                elif abs(a.x - b.x) < 0.5 and abs(a.y - b.y) > 5:
                    vs.append(((a.x + b.x) / 2, min(a.y, b.y), max(a.y, b.y)))
    return hs, vs


def _label_cluster(labels: list[_TextCluster]) -> list[_TextCluster]:
    """The compact group of field labels nearest the lower right sheet corner: labels joined while
    the gap between them is at most a few text heights. A drawing note elsewhere in the lower right
    quarter (SURFACE FINISH, GENERAL TOLERANCES) stays out of it."""
    heights = sorted(c.bbox[3] - c.bbox[1] for c in labels)
    reach = 6.0 * max(heights[len(heights) // 2], 4.0)
    seed = max(labels, key=lambda c: c.bbox[2] + c.bbox[3])
    group, todo = [seed], [seed]
    rest = [c for c in labels if c is not seed]
    while todo:
        cur = todo.pop()
        near = [c for c in rest if _rect_gap(cur.bbox, c.bbox) <= reach]
        rest = [c for c in rest if all(c is not n for n in near)]
        group += near
        todo += near
    return group


def _closed_cell(
    x: float, y: float, hs, vs, rect: tuple[float, float, float, float]
) -> tuple[float, float, float, float] | None:
    """The smallest box of table lines around a point inside `rect`, or None when a side is open."""
    above = [h for h, x0, x1 in hs if h < y and x0 - 1 <= x <= x1 + 1 and h >= rect[1] - 2]
    below = [h for h, x0, x1 in hs if h > y and x0 - 1 <= x <= x1 + 1]
    left = [v for v, y0, y1 in vs if v < x and y0 - 1 <= y <= y1 + 1 and v >= rect[0] - 2]
    right = [v for v, y0, y1 in vs if v > x and y0 - 1 <= y <= y1 + 1]
    if not (above and below and left and right):
        return None
    return (max(left), max(above), min(right), min(below))


def _generic_title_block(
    page: pymupdf.Page, clusters: list[_TextCluster]
) -> tuple[TitleBlock | None, tuple[float, float, float, float] | None, list[_TextCluster]]:
    """Title block of a non-Inventor sheet: a compact group of field labels (DRAWING NO., MATERIAL,
    WEIGHT, SCALE...) in the lower right part of the sheet; its rectangle reaches the sheet corner
    and snaps to the nearest table lines above and left of the labels.

    Also returns the texts with digits inside the rectangle that are no field of the table (not the
    value under a label, not in a closed table cell of their own): those are never cut away."""
    pw, ph = float(page.rect.width), float(page.rect.height)
    labels = [
        c for c in clusters
        if len(c.decoded_text) <= 40 and not _DIMLIKE_RE.search(c.decoded_text)
        and _TB_LABEL_RE.search(c.decoded_text) and c.center[1] > 0.55 * ph and c.center[0] > 0.35 * pw
        and abs(c.direction[0]) > 0.99
    ]
    if len(labels) < 3:
        return None, None, []
    labels = _label_cluster(labels)
    if len(labels) < 3:
        return None, None, []
    sx0 = min(c.bbox[0] for c in labels)
    sy0 = min(c.bbox[1] for c in labels)
    sx1 = max(c.bbox[2] for c in labels)
    sy1 = max(c.bbox[3] for c in labels)
    hs, vs = _segments(page.get_drawings())
    span_w, span_h = sx1 - sx0, sy1 - sy0
    tops = [y for y, x0, x1 in hs if sy0 - 30 <= y <= sy0 + 1 and min(x1, sx1) - max(x0, sx0) >= 0.4 * span_w]
    lefts = [x for x, y0, y1 in vs if sx0 - 30 <= x <= sx0 + 1 and min(y1, sy1) - max(y0, sy0) >= 0.3 * span_h]
    rect = (max(lefts) if lefts else sx0 - 6.0, max(tops) if tops else sy0 - 6.0, pw, ph)

    inside = [c for c in clusters if rect[0] - 1 <= c.center[0] <= rect[2] + 1 and rect[1] - 1 <= c.center[1] <= rect[3] + 1]
    label_ids = {id(c) for c in labels}

    def value_obj(label: _TextCluster) -> _TextCluster | None:
        h = label.bbox[3] - label.bbox[1]
        cands = [
            c for c in inside
            if id(c) not in label_ids and c is not label
            and label.bbox[1] + 0.5 * h - 1 <= c.bbox[1] <= label.bbox[3] + 2.5 * h
            and c.bbox[0] < label.bbox[2] + 3 * h and c.bbox[2] > label.bbox[0] - h
        ]
        return min(cands, key=lambda c: (c.bbox[1] - label.bbox[1]) + 0.2 * abs(c.bbox[0] - label.bbox[0]), default=None)

    def value_of(label: _TextCluster) -> str | None:
        best = value_obj(label)
        return best.decoded_text if best is not None else None

    tb = TitleBlock()
    for c in sorted(labels, key=lambda c: c.bbox[1]):
        if tb.nr_rysunku is None and _TB_NUMBER_LABEL_RE.search(c.decoded_text):
            tb.nr_rysunku = value_of(c)
        if tb.nazwa is None and _TB_NAME_LABEL_RE.search(c.decoded_text) and not _TB_NUMBER_LABEL_RE.search(c.decoded_text):
            tb.nazwa = value_of(c)
    for c in inside:
        t = c.decoded_text.strip()
        if tb.skala is None and re.match(r"^\d+(?:[.,]\d+)?\s*:\s*\d+(?:[.,]\d+)?$", t):
            tb.skala = t.replace(" ", "")
        if tb.arkusz is None and (m := re.match(r"^(?:SHEET\s+)?(\d+)\s*(?:/|OF|0F)\s*(\d+)$", t, re.IGNORECASE)):
            tb.arkusz = f"{m.group(1)} / {m.group(2)}"

    values = {id(v) for v in (value_obj(c) for c in labels) if v is not None}

    def is_field(c: _TextCluster) -> bool:
        if id(c) in values:
            return True
        cell = _closed_cell(c.center[0], c.center[1], hs, vs, rect)
        if cell is None:
            return False
        in_cell = [lb for lb in labels if cell[0] <= lb.center[0] <= cell[2] and cell[1] <= lb.center[1] <= cell[3]]
        return len(in_cell) <= 1  # a cell of the table, not its outer border around all fields

    loose = [c for c in inside if id(c) not in label_ids and _DIMLIKE_RE.search(c.decoded_text) and not is_field(c)]
    return tb, rect, loose


def _glyph_groups(page: pymupdf.Page) -> list[dict]:
    """Small vector paths that may be text symbols drawn as curves (SolidWorks draws Ø this way):
    paths up to 30 pt across with several segments, touching paths merged into one glyph."""
    small = []
    for d in page.get_drawings():
        r = d["rect"]
        if max(r.width, r.height) > 30:
            continue
        segs = []
        for it in d["items"]:
            if it[0] == "l":
                segs.append((it[1], it[2], True))
            elif it[0] == "c":  # Bezier: split at its midpoint, which lies on the curve
                p1, p2, p3, p4 = it[1], it[2], it[3], it[4]
                mid = pymupdf.Point((p1.x + 3 * p2.x + 3 * p3.x + p4.x) / 8, (p1.y + 3 * p2.y + 3 * p3.y + p4.y) / 8)
                segs += [(p1, mid, False), (mid, p4, False)]
            elif it[0] == "re":
                q = it[1]
                segs += [(q.tl, q.tr, True), (q.tr, q.br, True), (q.br, q.bl, True), (q.bl, q.tl, True)]
            elif it[0] == "qu":
                q = it[1]
                segs += [(q.ul, q.ur, True), (q.ur, q.lr, True), (q.lr, q.ll, True), (q.ll, q.ul, True)]
        small.append({"rect": pymupdf.Rect(r), "segs": segs, "fill": d.get("fill") is not None and d.get("color") is None})
    # merge touching paths (circle and slash may be separate paths)
    merged: list[dict] = []
    for g in sorted(small, key=lambda g: (g["rect"].x0, g["rect"].y0)):
        for m in merged:
            if m["rect"].intersects(g["rect"]) and max(m["rect"].width, m["rect"].height) <= 30:
                m["rect"] |= g["rect"]
                m["segs"] += g["segs"]
                m["fill"] = m["fill"] and g["fill"]
                break
        else:
            merged.append({"rect": pymupdf.Rect(g["rect"]), "segs": list(g["segs"]), "fill": g["fill"]})
    # filled triangles are arrowheads; a filled glyph outline has many more segments
    return [g for g in merged if len(g["segs"]) >= (5 if g["fill"] else 4) and max(g["rect"].width, g["rect"].height) <= 30]


def _is_diameter_glyph(g: dict, direction: tuple[float, float] = (1.0, 0.0)) -> bool:
    """A circle (many short segments all around one centre, as wide as high) crossed by one straight
    slash leaning forward like '/' in the reading frame of the text (`direction`), 40..85° to the
    baseline. A flat line with rounded ends or arrowheads, or a small hole with a centre line, is not."""
    straight = [(a, b) for a, b, is_line in g["segs"] if is_line]
    if not straight:
        return False
    sa, sb = max(straight, key=lambda s: math.dist(s[0], s[1]))
    slash = math.dist(sa, sb)
    pts = [p for a, b, _ in g["segs"] if (a, b) != (sa, sb) for p in (a, b)]
    if len(pts) < 16:
        return False
    cx = sum(p.x for p in pts) / len(pts)
    cy = sum(p.y for p in pts) / len(pts)
    radii = [math.dist((p.x, p.y), (cx, cy)) for p in pts]
    r = sum(radii) / len(radii)
    if r <= 0 or max(abs(x - r) for x in radii) > 0.2 * r:
        return False
    w = max(p.x for p in pts) - min(p.x for p in pts)
    h = max(p.y for p in pts) - min(p.y for p in pts)
    if not 0.8 <= w / max(h, 1e-6) <= 1.25:
        return False  # not round: a flat line with end caps
    angles = sorted(math.atan2(p.y - cy, p.x - cx) for p in pts)
    gaps = [b - a for a, b in zip(angles, angles[1:])] + [angles[0] + 2 * math.pi - angles[-1]]
    if max(gaps) > math.radians(60):
        return False  # an arc, not a whole circle
    mid = ((sa.x + sb.x) / 2, (sa.y + sb.y) / 2)
    if not (2.1 * r <= slash <= 4.0 * r and math.dist(mid, (cx, cy)) <= 0.35 * r):
        return False
    dx, dy = direction
    vx, vy = sb.x - sa.x, sb.y - sa.y
    along, up = vx * dx + vy * dy, vx * dy - vy * dx  # PDF y grows downwards: 'up' of the text is (dy, -dx)
    lean = math.degrees(math.atan2(up, along)) % 180.0
    return 40.0 <= lean <= 85.0


def _apply_curve_symbols(clusters: list[_TextCluster], glyphs: list[dict]) -> list[_TextCluster]:
    """Symbols drawn as curves just before a number: Ø is read into the text (with a multiplicity
    text like '2*' standing before it); any other unexplained symbol marks the type as uncertain."""
    out = list(clusters)
    consumed: set[int] = set()
    for i, c in enumerate(out):
        txt = c.decoded_text.strip()
        if not re.match(r"^\.?\d", txt):
            continue
        d = c.direction
        p = (-d[1], d[0])
        if c.start is not None and abs(d[0]) < 0.99 and abs(d[1]) < 0.99:
            # slanted text: its axis-aligned box sticks out before the first character, so the
            # start comes from the baseline origin of that character; a symbol before it has its
            # centre before that point (what overlaps the first character is no prefix)
            t0 = c.start[0] * d[0] + c.start[1] * d[1]
            t_end = t0 - 0.15 * c.size
        else:
            t0, _ = _project_interval(c.bbox, d)
            t_end = t0 + 0.1 * c.size
        cperp = c.center[0] * p[0] + c.center[1] * p[1]
        s = c.size
        before = []
        for g in glyphs:
            r = g["rect"]
            gc = ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
            tg = gc[0] * d[0] + gc[1] * d[1]
            if (t0 - 1.6 * s <= tg <= t_end and abs(gc[0] * p[0] + gc[1] * p[1] - cperp) <= 0.5 * s
                    and 0.3 * s <= max(r.width, r.height) <= 1.5 * s):
                before.append((t0 - tg, g, gc))
        if not before:
            continue
        _, g, gc = min(before, key=lambda b: b[0])
        if not _is_diameter_glyph(g, d):
            out[i] = _TextCluster(c.raw_text, c.decoded_text, c.bbox, c.direction, c.size, type_uncertain=True)
            continue
        text, bbox = "Ø" + txt, c.bbox
        tg0, _ = _project_interval(tuple(g["rect"]), d)
        for j, o in enumerate(out):  # '2*' or '4X' written before the symbol
            if j == i or id(o) in consumed or abs(o.direction[0] - d[0]) + abs(o.direction[1] - d[1]) > 0.05:
                continue
            _, o1 = _project_interval(o.bbox, d)
            operp = o.center[0] * p[0] + o.center[1] * p[1]
            if (m := re.match(r"^(\d+)\s*[xX×*]$", o.decoded_text.strip())) and -0.3 * s <= tg0 - o1 <= 0.8 * s \
                    and abs(operp - cperp) <= 0.5 * s:
                text = f"{m.group(1)}x{text}"
                bbox = (min(bbox[0], o.bbox[0]), min(bbox[1], o.bbox[1]), max(bbox[2], o.bbox[2]), max(bbox[3], o.bbox[3]))
                consumed.add(id(o))
                break
        out[i] = _TextCluster(c.raw_text, text, bbox, c.direction, c.size)
    return [c for c in out if id(c) not in consumed]


def _inch_units(texts: list[str]) -> bool:
    """Inch drawings: a units note, or decimals without a leading zero (ASME inch style .250)."""
    joined = " ".join(texts)
    if re.search(r"\bUNITS?\s*:?\s*INCH(?:ES)?\b|\bINCHES\b", joined, re.IGNORECASE):
        return True
    # 'UNITS: IN' - but 'IN' followed by a word is the preposition (UNITS IN MM, UNIT IN MILLIMETERS)
    if any(re.search(r"\bUNITS?\s*:?\s*IN\b(?!\s*[A-Za-z])", t, re.IGNORECASE) for t in texts):
        return True
    return sum(1 for t in texts if re.search(r"(?:^|[\sØR±+-])\.\d", t)) >= 2


_NO_TEXT = ("strona {n} nie zawiera tekstu wektorowego (tekst zamieniony na krzywe albo skan): "
            "wymiarów nie da się odczytać")
_NO_DIMS = ("na stronie {n} nie znaleziono żadnego wymiaru w tekście wektorowym (tekst wymiarów "
            "zamieniony na krzywe albo nieczytelny): wymiarów nie da się odczytać")
_ISOMETRIC = ("strona {n}: większość tekstu biegnie wzdłuż osi aksonometrycznych (widok 3D z adnotacjami PMI, "
              "nie rzut prostokątny): takich wymiarów program nie odczytuje pewnie")


def _part_warnings(dims: list[ExtractedDimension], page_idx: int, clusters: list[_TextCluster]) -> list[str]:
    if not dims:
        return [_NO_DIMS.format(n=page_idx + 1)]
    # Radius and diameter labels follow their leaders at any angle; on an isometric (PMI) view most
    # of the text, labels and axis letters included, runs along the isometric axes (30°, 60°...).
    angles = [math.degrees(math.atan2(c.direction[1], c.direction[0])) % 180.0 for c in clusters]
    iso = [a for a in angles if min(abs(a - k) for k in (30.0, 60.0, 120.0, 150.0)) <= 2.0]
    if len(iso) >= 5 and 2 * len(iso) >= len(angles):
        return [_ISOMETRIC.format(n=page_idx + 1)]
    return []


_INCHES = ("rysunek w calach (UNITS: INCHES albo liczby bez zera przed kropką, np. .250): "
           "program przyjmuje milimetry, wartości nie zostały przeliczone")


def _render_region_png(
    page: pymupdf.Page,
    bbox: tuple[float, float, float, float],
    out_path: Path,
    dpi: int = 200,
) -> str:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    clip = pymupdf.Rect(*bbox)
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    pix.save(str(out_path))
    return str(out_path)


# markers of texts in PartRegion.raw_texts that were cut away from the drawing area on purpose
_MARK_ZONE = "[ramka]"
_MARK_TABLE = "[tabelka]"


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", text)


def extract_pdf(
    pdf_path: str | Path,
    render_dir: str | Path | None = None,
    dpi: int = 200,
) -> ExtractedDocument:
    """Extract all part regions, dimensions, title blocks, and BOM from a vector PDF."""
    pdf_path = Path(pdf_path)
    doc = pymupdf.open(str(pdf_path))
    parts: list[PartRegion] = []
    all_bom: list[BomItem] = []
    assembly_tb: TitleBlock | None = None
    assembly_dims: list[ExtractedDimension] = []
    is_assembly = False
    doc_warnings: list[str] = []
    page_texts: list[str] = []

    used_names: set[str] = set()

    def render(page, bbox, name: str) -> str | None:
        if render_dir is None:
            return None
        # one image per part: sheets of one drawing number must not overwrite each other
        base = name = _safe_name(name)
        if name.lower() in used_names:
            base = name = f"{name}_s{page.number + 1}"
            k = 2
            while name.lower() in used_names:
                name, k = f"{base}_{k}", k + 1
        used_names.add(name.lower())
        return _render_region_png(page, bbox, Path(render_dir) / f"{name}.png", dpi=dpi)

    try:
        for page_idx, page in enumerate(doc):
            pw, ph = float(page.rect.width), float(page.rect.height)
            spans = _extract_spans(page)
            if not spans:
                # drawn or a raster image, but without any text: text turned into curves, or a scan
                if page.get_drawings() or page.get_images():
                    parts.append(PartRegion(
                        page_index=page_idx, bbox=(0.0, 0.0, pw, ph),
                        image_path=render(page, (0.0, 0.0, pw, ph), f"p{page_idx + 1}"),
                        warnings=[_NO_TEXT.format(n=page_idx + 1)],
                    ))
                continue
            page_clusters = _cluster_spans(spans)
            page_texts.extend(c.decoded_text for c in page_clusters)
            # frame zone labels (1..8, A..F) are never part of a drawing area
            zone_ids = _frame_zone_labels(page_clusters, pw, ph)
            zones = [c for c in page_clusters if id(c) in zone_ids]
            excluded = [c.bbox for c in zones]

            def outside(s: _RawSpan, rects=excluded, keep=()) -> bool:
                def within(r) -> bool:
                    return r[0] - 0.5 <= s.cx <= r[2] + 0.5 and r[1] - 0.5 <= s.cy <= r[3] + 0.5
                return any(within(r) for r in keep) or not any(within(r) for r in rects)

            def zone_marks(bbox) -> list[str]:  # cut labels stay visible, marked
                return [f"{_MARK_ZONE} {c.decoded_text}" for c in zones
                        if bbox[0] <= c.center[0] <= bbox[2] and bbox[1] <= c.center[1] <= bbox[3]]

            glyphs = _glyph_groups(page)
            page_regions = _detect_page_regions(page)

            if len(page_regions) > 1:
                # Multi-part sheet: one region per part, separated by divider lines
                for reg_idx, bbox in enumerate(page_regions):
                    reg_spans = [s for s in spans if _span_in_region(s, bbox) and outside(s)]
                    clusters = _apply_curve_symbols(_cluster_spans(reg_spans), glyphs)
                    if not clusters:
                        continue

                    drawing_no: str | None = None
                    name: str | None = None
                    for c in clusters:
                        if m := _PART_HEADER_RE.match(c.decoded_text):
                            name = m.group("name").strip()
                            drawing_no = m.group("drawing_no").strip()
                    dims, unrecognized, others = _parse_region(
                        clusters, skip=lambda c: bool(_PART_HEADER_RE.match(c.decoded_text)))

                    parts.append(
                        PartRegion(
                            page_index=page_idx,
                            drawing_no=drawing_no,
                            name=name,
                            bbox=bbox,
                            dimensions=dims,
                            image_path=render(page, bbox, drawing_no or f"p{page_idx + 1}_r{reg_idx + 1}"),
                            raw_texts=[c.decoded_text for c in clusters] + zone_marks(bbox),
                            unrecognized_texts=unrecognized + others,
                            warnings=_part_warnings(dims, page_idx, clusters),
                        )
                    )
            else:
                # Single-part sheet OR assembly drawing (with title block & optional BOM)
                all_clusters = page_clusters
                tb_rect = _title_block_rect(page)
                tb, tb_top = _parse_title_block(all_clusters, ph, tb_rect)
                bom, bom_top = _parse_bom(page, spans, tb_top + 4.0)
                content_bottom = min(tb_top, bom_top)

                if bom:
                    is_assembly = True
                    all_bom.extend(bom)
                    assembly_tb = tb
                    # assembly dimensions (e.g. overall height), without item balloon numbers
                    balloons = _balloon_circles(page)
                    content = (0.0, 0.0, pw, content_bottom)
                    asm_spans = [
                        s for s in spans
                        if _span_in_region(s, content) and outside(s)
                        and not any(math.hypot(s.cx - bx, s.cy - by) <= br for bx, by, br in balloons)
                    ]
                    for c in _cluster_spans(asm_spans):
                        dim = parse_dimension_token(c, f"Z{len(assembly_dims) + 1}")
                        if dim is not None:
                            assembly_dims.append(dim)
                    continue

                # title block of other programs (SolidWorks, AutoCAD...): field labels in the corner
                rects = excluded
                keep: list[tuple[float, float, float, float]] = []
                if tb is None:
                    tb, generic_rect, loose = _generic_title_block(
                        page, [c for c in all_clusters if id(c) not in zone_ids])
                    if generic_rect is not None:
                        rects = [*excluded, generic_rect]
                        keep = [c.bbox for c in loose]  # numbers that are no field of the table

                # Single-part drawing sheet: exclude title block from dimension area
                # text sticking out of the page box is still text of the drawing
                inf = float("inf")
                content_bbox = (-inf, -inf, inf, content_bottom if content_bottom < ph else inf)
                content_spans = [s for s in spans if _span_in_region(s, content_bbox) and outside(s, rects, keep)]
                content_clusters = _apply_curve_symbols(_cluster_spans(content_spans), glyphs)
                # texts of the title block stay visible in raw_texts, marked
                content_ids = {id(s) for s in content_spans}
                table_marks = [
                    f"{_MARK_TABLE} {c.decoded_text}" for c in _cluster_spans(
                        [s for s in spans if id(s) not in content_ids and outside(s)])
                ]

                dims, unrecognized, others = _parse_region(content_clusters)

                drawing_no = tb.nr_rysunku if tb else None
                name = tb.nazwa if tb else None

                parts.append(
                    PartRegion(
                        page_index=page_idx,
                        drawing_no=drawing_no,
                        name=name,
                        bbox=(0.0, 0.0, pw, ph),
                        dimensions=dims,
                        title_block=tb,
                        image_path=render(page, (0.0, 0.0, pw, ph), drawing_no or f"p{page_idx + 1}"),
                        raw_texts=[c.decoded_text for c in content_clusters] + zone_marks((0.0, 0.0, pw, ph))
                        + table_marks,
                        unrecognized_texts=unrecognized + others,
                        warnings=_part_warnings(dims, page_idx, content_clusters),
                    )
                )
    finally:
        doc.close()

    if _inch_units(page_texts):
        parts = [p.model_copy(update={"warnings": [*p.warnings, _INCHES]}) for p in parts]
    single_sheets = [p for p in parts if p.title_block is not None or p.bbox[2] - p.bbox[0] >= 0.9 * max(p.bbox[2], 1.0)]
    if len(parts) > 1 and len(single_sheets) == len(parts) and len({p.drawing_no for p in parts}) < len(parts):
        doc_warnings.append(
            f"Liczba stron bez rozróżnialnych numerów rysunku: {len(parts)}. Każda strona jest traktowana jako "
            "osobna część, choć mogą to być arkusze jednej części."
        )

    return ExtractedDocument(
        source_pdf=str(pdf_path),
        parts=parts,
        bom=all_bom,
        assembly_title_block=assembly_tb,
        assembly_dimensions=assembly_dims,
        is_assembly=is_assembly,
        warnings=doc_warnings,
    )
