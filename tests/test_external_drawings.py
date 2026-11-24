"""Drawings from other CAD programs than Inventor (SolidWorks, Fusion, AutoCAD, NIST PMI).

Nothing with a number may disappear silently: every text with digits in the part area is a
dimension, a tolerance of one, or is listed as unrecognized. Drawings whose text was turned
into curves give a clear extraction warning and never reach the AI."""
from __future__ import annotations

import math
import re
from pathlib import Path

import pymupdf
import pytest

from drawing2cad.pdf_extract import _TextCluster, extract_pdf, parse_dimension_token
from drawing2cad.pipeline import process_part

REPO_ROOT = Path(__file__).resolve().parent.parent
EXT = REPO_ROOT / "rysunki-zewnetrzne"
PP = EXT / "precious-plastic"
CR = EXT / "creality-ender3"
NIST = EXT / "nist" / "definicje_2d"


def _part(pdf: Path):
    doc = extract_pdf(pdf)
    assert len(doc.parts) == 1, [p.drawing_no for p in doc.parts]
    return doc.parts[0]


def _c(text: str) -> _TextCluster:
    return _TextCluster(raw_text=text, decoded_text=text, bbox=(0, 0, 10, 5), direction=(1.0, 0.0), size=10.0)


def _dims(part) -> list[tuple[str, str, float]]:
    return sorted((d.dim_type, d.decoded_text, d.value) for d in part.dimensions)


# --- 1. decimal point vs drawing number --------------------------------------------------------

def test_decimal_point_is_a_number_but_drawing_numbers_stay_rejected():
    assert parse_dimension_token(_c("7.85"), "D1").value == 7.85
    assert parse_dimension_token(_c("0.5"), "D1").value == 0.5
    for text in ("01.01.03", "4.10.002-1", "12-345"):
        assert parse_dimension_token(_c(text), "D1") is None, text


def test_eccentric_column_keeps_every_decimal_dimension():
    part = _part(CR / "Eccentric_column.pdf")
    values = sorted(d.value for d in part.dimensions)
    assert values == [0.5, 0.7, 2.4, 5.0, 7.2, 7.2, 7.85, 10.0], _dims(part)


def test_e_plate_keeps_decimal_ordinates():
    part = _part(CR / "E_plate.pdf")
    values = {d.value for d in part.dimensions}
    for v in (23.5, 9.5, 11.9, 14.35, 24.45, 33.25, 47.5, 52.1, 22.9, 4.5, 1.5, 2.5):
        assert v in values, (v, _dims(part))


TEXT_PDFS = sorted(p for p in EXT.rglob("*.pdf") if "LICENSE" not in p.name) + sorted((REPO_ROOT / "rysunki").glob("*.pdf"))


def _accounted(doc, page_index: int) -> set[str]:
    """Every text (whitespace removed) the extraction gave for one page: dimensions, single limits of
    their tolerances, unrecognized texts and their pieces, texts cut away on purpose (marked), BOM."""
    out: set[str] = set()

    def add(t: str) -> None:
        out.add(re.sub(r"\s+", "", t))

    for part in (p for p in doc.parts if p.page_index == page_index):
        for d in part.dimensions:
            add(d.decoded_text)
            if d.decoded_text.startswith("Ø"):  # symbol drawn as curves, read into the text
                add(d.decoded_text[1:])
            if m := re.match(r"^(\d+)x(Ø.*)$", d.decoded_text):  # '2*' before a curve symbol
                add(f"{m.group(1)}*")
                add(f"{m.group(1)}x")
                add(m.group(2)[1:])
            for piece in re.split(r"[/\s]+", d.tolerance or ""):
                add(piece)
        for u in part.unrecognized_texts:
            for v in (u, u[1:] if u.startswith("Ø") else u):
                add(v)
                for piece in re.split(r"[/\s]+", v):
                    add(piece)
                halves = v.split("/")  # stacked limits joined to the text: 'G4 +0,011/+0,006'
                for k in range(1, len(halves)):
                    add("/".join(halves[:k]))
        for r in part.raw_texts:
            if m := re.match(r"^\[(?:ramka|tabelka)\] (.*)$", r):
                add(m.group(1))
    for d in doc.assembly_dimensions:
        add(d.decoded_text)
    return out


@pytest.mark.parametrize("pdf", TEXT_PDFS, ids=lambda p: p.name)
def test_no_text_with_digits_disappears_silently(pdf):
    """Safety net: each text with a digit on the page, read straight from the page before any frame
    or title block filter, is a dimension, a limit of one, listed as unrecognized, or marked as cut
    away in raw_texts (part headers, view scale labels and web addresses aside). Exact match only."""
    from drawing2cad.pdf_extract import _cluster_spans, _extract_spans

    doc = extract_pdf(pdf)
    if doc.is_assembly:
        pytest.skip("assembly sheet: BOM and balloons are checked by their own tests")
    with pymupdf.open(pdf) as raw:
        for page in raw:
            if not any(p.page_index == page.number for p in doc.parts):
                continue
            known = _accounted(doc, page.number)
            for c in _cluster_spans(_extract_spans(page)):
                t = c.decoded_text.strip()
                if not re.search(r"\d", t):
                    continue
                if re.search(r",\s*rys\.", t) or re.match(r"^(?:SCALE|SKALA)\s*\d+(?:[.,]\d+)?\s*:\s*\d+", t, re.I) \
                        or t.startswith("www."):
                    continue
                assert re.sub(r"\s+", "", t) in known, (pdf.name, page.number, t)


# --- 2. text turned into curves ----------------------------------------------------------------

@pytest.mark.parametrize("pdf", [PP / "v2_03_shredding_axe.pdf", PP / "v2_07_injection_framework_plunger_A4.pdf",
                                 PP / "v2_09_extrusion_barrel_shaft.pdf", NIST / "ctc" / "nist_ctc_01_asme1_rd.pdf"],
                         ids=lambda p: p.name)
def test_drawing_without_vector_text_gives_a_warning(pdf):
    doc = extract_pdf(pdf)
    assert doc.parts, "every drawn page must give a part, even without text"
    for part in doc.parts:
        assert not part.dimensions
        assert any("tekst" in w and "wymiar" in w for w in part.warnings), part.warnings


class _NoAI:
    def interpret(self, *a, **kw):  # pragma: no cover - must not be called
        raise AssertionError("AI called for a drawing without readable dimensions")


def test_pipeline_reports_error_without_calling_ai(tmp_path):
    part = extract_pdf(PP / "v2_03_shredding_axe.pdf").parts[0]
    report, spec = process_part(part, _NoAI(), out_dir=tmp_path)
    assert report.status == "BŁĄD" and spec is None
    assert any("tekst" in e for e in report.errors)


def test_inch_drawing_is_flagged():
    doc = extract_pdf(NIST / "ftc" / "nist_ftc_07_asme1_rd.pdf")
    assert all(any("cal" in w for w in p.warnings) for p in doc.parts)


def test_isometric_pmi_pages_never_reach_the_ai():
    """NIST PMI: 3D views with slanted annotation, five pages of one part."""
    doc = extract_pdf(NIST / "ftc" / "nist_ftc_10_asme1_rb.pdf")
    assert len(doc.parts) == 5
    assert doc.parts[1].warnings and doc.parts[2].warnings  # text along the isometric axes ('43X' merged there)
    assert any("arkusze jednej części" in w for w in doc.warnings)  # the rest: NIEPEWNE at best


def test_inclined_aligned_dimensions_are_not_isometric():
    for pdf in (REPO_ROOT / "rysunki" / "cad_07_1.pdf", PP / "SHRPRO-V1_1420.10-A_Gusset_plate.pdf"):
        assert not any(p.warnings for p in extract_pdf(pdf).parts), pdf.name


# --- 3. frame zones and non-Inventor title blocks ----------------------------------------------

def test_solidworks_frame_zones_and_title_block_are_not_dimensions():
    part = _part(PP / "SHRPRO-V1_1000.02-A_Bearing-coupling_spacer_10mm.pdf")
    assert _dims(part) == sorted([("dlugosc", "10", 10.0), ("dlugosc", "15", 15.0), ("dlugosc", "26,6", 26.6),
                                  ("srednica", "Ø65,2", 65.2), ("promien", "R22,6", 22.6)])
    assert part.drawing_no == "SHRPRO-V1 1000.02"
    assert part.name == "Bearing-coupling spacer 10mm"
    assert part.title_block.skala == "1:1"


@pytest.mark.parametrize("name", ["Heat_block.pdf", "Spacers_for_rollers.pdf", "Eccentric_column.pdf"])
def test_creality_title_block_and_zones_excluded(name):
    part = _part(CR / name)
    texts = {d.decoded_text for d in part.dimensions} | set(part.unrecognized_texts)
    assert not any("6061" in t or t in {"A4", "5:1", "GB/T 1804-m"} for t in texts), texts
    zones = [d.decoded_text for d in part.dimensions if d.bbox[1] < 40 or d.bbox[0] < 30]  # frame zone numbers
    assert zones == []
    assert part.title_block is not None and part.drawing_no


# --- 4. stacked tolerances and fits ------------------------------------------------------------

def test_stacked_tolerances_and_fits_on_the_shaft():
    part = _part(PP / "SHRPRO-V1_1120.13-A_Shaft_1.pdf")
    by_val = {}
    for d in part.dimensions:
        by_val.setdefault(d.value, []).append(d)
    assert [d.tolerance for d in by_val[610.0]] == ["+0,01/-0,01"]
    assert [d.tolerance for d in by_val[22.5]] == ["+0,005/-0,005"]
    assert [d.tolerance for d in by_val[300.0]] == ["0,00/-0,01"]
    shafts = by_val[45.0]
    assert sorted((d.dim_type, d.tolerance) for d in shafts if d.tolerance) == [("srednica", "g6 -0,009/-0,025")] * 2
    assert "G4 +0,011/+0,006" in part.unrecognized_texts
    assert not any(re.fullmatch(r"[+-]?0,0\d+", u) for u in part.unrecognized_texts), part.unrecognized_texts
    assert 9.0 not in by_val  # mass from the title block


# --- 5. diameter symbol drawn as curves --------------------------------------------------------

def test_curve_diameter_symbol_is_read():
    part = _part(CR / "Eccentric_column.pdf")
    kinds = {(d.dim_type, d.value) for d in part.dimensions}
    assert ("srednica", 5.0) in kinds and ("srednica", 7.2) in kinds
    assert ("dlugosc", 5.0) not in kinds


def _sheet_with_symbol(path: Path, symbol: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((300, 400), "12", fontsize=10)
    cx, cy, r = 293.0, 396.5, 3.2
    if symbol == "diameter":
        pts = [(cx + r * math.cos(a * math.pi / 24), cy + r * math.sin(a * math.pi / 24)) for a in range(49)]
        page.draw_polyline(pts, color=(0, 0, 0), width=0.5)  # circle and slash as two paths
        page.draw_line((cx - 2.0, cy + 4.5), (cx + 2.0, cy - 4.5), color=(0, 0, 0), width=0.5)  # '/' at ~66°
    elif symbol == "square":
        page.draw_rect(pymupdf.Rect(cx - 3, cy - 3, cx + 3, cy + 3), color=(0, 0, 0), width=0.5)
    page.draw_line((100, 300), (500, 300), color=(0, 0, 0), width=0.5)
    doc.save(str(path))


@pytest.mark.parametrize(("symbol", "dtype", "uncertain"), [
    ("diameter", "srednica", False), ("square", "dlugosc", True), (None, "dlugosc", False)])
def test_symbol_before_number(tmp_path, symbol, dtype, uncertain):
    pdf = tmp_path / "s.pdf"
    _sheet_with_symbol(pdf, symbol)
    (d,) = extract_pdf(pdf).parts[0].dimensions
    assert (d.dim_type, d.value, d.type_uncertain) == (dtype, 12.0, uncertain)


# --- 6. smaller forms --------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "dtype", "value", "mult", "spec"), [
    ("2*M3", "gwint", 3.0, 2, "M3"),
    ("M30*1.5", "gwint", 30.0, 1, "M30x1.5"),
    ("4X Ø10", "srednica", 10.0, 4, None),
    (".250", "dlugosc", 0.25, 1, None),
])
def test_star_multiplication_and_inch_decimals(text, dtype, value, mult, spec):
    d = parse_dimension_token(_c(text), "D1")
    assert d is not None, text
    assert (d.dim_type, d.value, d.multiplicity, d.thread_spec) == (dtype, value, mult, spec)


def test_e_plate_star_multiplicity():
    part = _part(CR / "E_plate.pdf")
    kinds = {(d.dim_type, d.value, d.multiplicity) for d in part.dimensions}
    assert ("gwint", 3.0, 2) in kinds
    assert ("srednica", 5.0, 2) in kinds and ("srednica", 5.4, 2) in kinds


def test_uncertain_type_may_be_checked_as_a_diameter_and_document_notes_make_parts_uncertain():
    from drawing2cad.pipeline import flag_document_warnings
    from drawing2cad.schema import ExtractedDimension, VerificationReport
    from drawing2cad.verifier import allowed_targets

    d = ExtractedDimension(id="D1", raw_text="12", decoded_text="12", dim_type="dlugosc", value=12.0, bbox=(0, 0, 1, 1))
    assert "walec_srednica" not in allowed_targets(d)
    assert "walec_srednica" in allowed_targets(d.model_copy(update={"type_uncertain": True}))

    doc = extract_pdf(NIST / "ftc" / "nist_ftc_10_asme1_rb.pdf")
    ok = VerificationReport(drawing_no="x", name="x", status="OK", passed=True)
    flagged = flag_document_warnings(ok, doc)
    assert flagged.status == "NIEPEWNE" and any(a.id.startswith("PROG-DOKUMENT") for a in flagged.ambiguities)


# --- 7. frame zone labels: only a real row of labels along the sheet edge ----------------------

A3 = (1191.0, 842.0)


def _sheet(path: Path, texts, size=A3, lines=(), rects=(), pages=1) -> None:
    """Synthetic vector sheet: texts as (x, y, text[, page]) baseline starts, lines, rectangles."""
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=size[0], height=size[1])
        for item in texts:
            if len(item) == 4 and item[3] != n:
                continue
            page.insert_text((item[0], item[1]), item[2], fontsize=10)
        for a, b in lines:
            page.draw_line(a, b, color=(0, 0, 0), width=0.5)
        for r in rects:
            page.draw_rect(pymupdf.Rect(*r), color=(0, 0, 0), width=0.5)
    doc.save(str(path))


def test_two_numbers_near_the_edge_are_dimensions_not_zone_labels(tmp_path):
    pdf = tmp_path / "s.pdf"
    _sheet(pdf, [(300, 50, "10"), (500, 50, "11"), (600, 400, "200")], lines=[((100, 300), (900, 300))])
    values = sorted(d.value for d in _part(pdf).dimensions)
    assert values == [10.0, 11.0, 200.0]


def test_dimension_in_the_margin_does_not_join_the_zone_row(tmp_path):
    pdf = tmp_path / "s.pdf"
    zones = [(70 + 149 * k, 18, str(8 - k)) for k in range(8)] + [(70 + 149 * k, 831, str(8 - k)) for k in range(8)]
    zones += [(10, 74 + 140 * k, "FEDCBA"[k]) for k in range(6)]
    _sheet(pdf, [*zones, (400, 58, "5"), (600, 400, "200")], lines=[((100, 300), (900, 300))])
    part = _part(pdf)
    assert sorted(d.value for d in part.dimensions) == [5.0, 200.0]
    marked = [t for t in part.raw_texts if t.startswith("[ramka]")]
    assert len(marked) == 22, marked  # cut zone labels stay visible in raw_texts


def test_solidworks_zone_row_has_28_labels_without_the_revision_letter():
    from drawing2cad.pdf_extract import _cluster_spans, _extract_spans, _frame_zone_labels

    with pymupdf.open(PP / "SHRPRO-V1_1000.02-A_Bearing-coupling_spacer_10mm.pdf") as doc:
        page = doc[0]
        clusters = _cluster_spans(_extract_spans(page))
        ids = _frame_zone_labels(clusters, page.rect.width, page.rect.height)
    cut = [c for c in clusters if id(c) in ids]
    assert len(cut) == 28
    assert not any(c.decoded_text == "A" and 1100 < c.center[0] < 1160 for c in cut)


# --- 8. non-Inventor title block: a compact cluster of labels, not every note on the sheet ------

_A4 = (842.0, 595.0)
_TB = [(610, 515, "DRAWING NO."), (610, 530, "PART-001"), (700, 515, "MATERIAL"), (700, 530, "STEEL"),
       (610, 560, "SCALE"), (610, 575, "1:1"), (700, 560, "SHEET"), (700, 575, "1 / 1")]


def test_note_far_from_the_title_block_does_not_swallow_dimensions(tmp_path):
    pdf = tmp_path / "s.pdf"
    _sheet(pdf, [*_TB, (320, 340, "SURFACE FINISH"), (450, 400, "42.5"), (500, 450, "Ø18"), (100, 200, "30")],
           size=_A4, rects=[(600, 500, 830, 585)], lines=[((100, 300), (300, 300))])
    part = _part(pdf)
    assert part.drawing_no == "PART-001"
    kinds = {(d.dim_type, d.value) for d in part.dimensions}
    assert {("dlugosc", 42.5), ("srednica", 18.0), ("dlugosc", 30.0)} <= kinds, kinds
    assert not any(d.value == 1.0 for d in part.dimensions)


def test_number_inside_the_title_block_area_that_is_no_field_is_not_lost(tmp_path):
    pdf = tmp_path / "s.pdf"
    _sheet(pdf, [*_TB, (790, 545, "17"), (100, 200, "30")], size=_A4, rects=[(600, 500, 830, 585)],
           lines=[((100, 300), (300, 300))])
    part = _part(pdf)
    assert part.drawing_no == "PART-001"
    assert 17.0 in {d.value for d in part.dimensions} or "17" in part.unrecognized_texts
    assert any(t.startswith("[tabelka]") and "PART-001" in t for t in part.raw_texts)


# --- 9. diameter symbol before slanted text ----------------------------------------------------

@pytest.mark.parametrize(("name", "value"), [
    ("SHRPRO-V1_1000.04-A_Motor_mounting_plate.pdf", 15.5),
    ("SHRPRO-V1_1110.05-A_Bearing_side_plate_1.pdf", 78.0),
    ("SHRPRO-V1_1110.14-A_M12_Threaded_rod_box_1.pdf", 12.0),
    ("SHRPRO-V1_1120.14-A_Spacer_4mm.pdf", 91.0),
])
def test_curve_diameter_before_slanted_text(name, value):
    part = _part(PP / name)
    found = [d for d in part.dimensions if d.value == value]
    assert found and all(d.dim_type == "srednica" and not d.type_uncertain for d in found), _dims(part)


# --- 10. raster scan: a clear error, never an empty success ------------------------------------

def _scan(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(20, 20, 575, 822), pixmap=pix)
    doc.save(str(path))


def test_raster_scan_gives_an_error_without_calling_ai(tmp_path):
    from drawing2cad.cli import main
    from drawing2cad.pipeline import process_pdf

    pdf = tmp_path / "scan.pdf"
    _scan(pdf)
    doc = extract_pdf(pdf)
    assert len(doc.parts) == 1 and any("tekst" in w for w in doc.parts[0].warnings)
    result = process_pdf(pdf, tmp_path / "out", _NoAI())
    assert [r.status for r in result.reports] == ["BŁĄD"]
    assert main([str(pdf), "--extract-only", "--out", str(tmp_path / "e")]) != 0
    assert main([str(pdf), "--reczne", str(tmp_path), "--out", str(tmp_path / "r"), "--no-html"]) != 0


# --- 11. curve glyphs that are not a diameter symbol -------------------------------------------

def _sheet_with_glyph(path: Path, kind: str, text: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((300, 400), text, fontsize=10)
    cx, cy = 291.0, 396.5
    if kind == "flat":  # short line with rounded ends, 13 x 1.2 pt, one path
        shape = page.new_shape()
        for side in (-1, 1):
            pts = [(cx + side * (6 + 0.6 * math.cos(a * math.pi / 6)), cy + 0.6 * math.sin(a * math.pi / 6))
                   for a in range(13)]
            shape.draw_polyline(pts)
        shape.draw_line((cx - 6.6, cy), (cx + 6.6, cy))
        shape.finish(color=(0, 0, 0), width=0.3, closePath=False)
        shape.commit()
    else:  # small hole with one centre line
        r = 3.5
        pts = [(cx + r * math.cos(a * math.pi / 24), cy + r * math.sin(a * math.pi / 24)) for a in range(49)]
        page.draw_polyline(pts, color=(0, 0, 0), width=0.5)
        ang = math.radians({"axis1": -1.0, "axis45": 45.0}[kind])  # PDF y grows downwards: +45° is '\'
        dx, dy = 4.5 * math.cos(ang), 4.5 * math.sin(ang)
        page.draw_line((cx - dx, cy - dy), (cx + dx, cy + dy), color=(0, 0, 0), width=0.5)
    page.draw_line((100, 300), (500, 300), color=(0, 0, 0), width=0.5)
    doc.save(str(path))


@pytest.mark.parametrize(("kind", "text"), [("flat", "5"), ("axis1", "12"), ("axis45", "12")])
def test_glyph_that_is_not_a_diameter_symbol_is_never_a_silent_diameter(tmp_path, kind, text):
    pdf = tmp_path / "g.pdf"
    _sheet_with_glyph(pdf, kind, text)
    (d,) = extract_pdf(pdf).parts[0].dimensions
    assert (d.dim_type, d.value, d.type_uncertain) == ("dlugosc", float(text), True)


# --- 12. loose tolerance texts -----------------------------------------------------------------

def _tc(text, bbox, direction=(1.0, 0.0)):
    return _TextCluster(raw_text=text, decoded_text=text, bbox=bbox, direction=direction, size=10.0)


def test_loose_deviation_goes_to_the_dimension_with_the_same_direction():
    from drawing2cad.pdf_extract import _parse_region

    dims, unknown, _ = _parse_region([
        _tc("20", (100, 100, 114, 110)), _tc("15", (120, 108, 130, 122), (0.0, -1.0)),
        _tc("+0,1", (114, 98, 122, 108), (0.0, -1.0))])
    tol = {d.value: d.tolerance for d in dims}
    assert tol == {20.0: None, 15.0: "+0,1"}, tol


def test_lone_zero_ordinate_is_not_a_tolerance():
    from drawing2cad.pdf_extract import _parse_region

    dims, unknown, _ = _parse_region([_tc("12", (100, 100, 112, 110)), _tc("0", (118, 105, 124, 115))])
    assert [d.tolerance for d in dims] == [None] and unknown == ["0"]


def test_complete_tolerance_takes_no_more_limits():
    from drawing2cad.pdf_extract import _parse_region

    dims, unknown, _ = _parse_region([_tc("12 ±0,1", (100, 100, 128, 110)), _tc("0", (130, 100, 136, 110))])
    assert [d.tolerance for d in dims] == ["±0,1"] and unknown == ["0"]


def test_zero_with_a_second_limit_is_a_stacked_pair():
    from drawing2cad.pdf_extract import _parse_region

    dims, unknown, _ = _parse_region([_tc("300", (100, 100, 118, 110)), _tc("0", (120, 97, 126, 104)),
                                      _tc("-0,1", (120, 105, 134, 112))])
    assert [d.tolerance for d in dims] == ["0/-0,1"] and unknown == []


# --- 13. units note ----------------------------------------------------------------------------

def test_units_in_mm_is_not_inches():
    from drawing2cad.pdf_extract import _inch_units

    assert not _inch_units(["UNITS IN MM", "10", "20"])
    assert not _inch_units(["UNIT IN MILLIMETERS"])
    assert not _inch_units(["UNITS", "IN ACCORDANCE WITH ISO 2768"])
    for t in ("UNITS: IN", "UNITS: IN.", "UNITS: INCHES", "UNITS IN INCHES"):
        assert _inch_units([t, "10"]), t


# --- 14. several sheets with the same drawing number -------------------------------------------

def _two_sheets(pdf: Path) -> None:
    _sheet(pdf, [*_TB, (100, 200, "30", 0), (100, 200, "45", 1)], size=_A4, rects=[(600, 500, 830, 585)],
           lines=[((100, 300), (300, 300))], pages=2)


def test_pages_with_the_same_drawing_number_get_their_own_images(tmp_path):
    pdf = tmp_path / "two.pdf"
    _two_sheets(pdf)
    doc = extract_pdf(pdf, render_dir=tmp_path / "img")
    a, b = doc.parts
    assert a.drawing_no == b.drawing_no == "PART-001"
    assert a.image_path != b.image_path
    assert Path(a.image_path).read_bytes() != Path(b.image_path).read_bytes()


def test_pipeline_keeps_one_result_per_sheet_with_the_same_drawing_number(tmp_path, monkeypatch):
    """End to end through process_pdf: document notes (pages that may be sheets of one part) make an
    otherwise OK part NIEPEWNE; each sheet keeps its own STEP name and spec."""
    from drawing2cad import pipeline
    from drawing2cad.schema import VerificationReport

    pdf = tmp_path / "two.pdf"
    _two_sheets(pdf)
    ids = []

    def fake(part, interpreter, out_dir, max_attempts=3, pdf_path=None, part_id=None):
        ids.append(part_id)
        return VerificationReport(drawing_no=part_id or "?", name="x", status="OK", passed=True), object()

    monkeypatch.setattr(pipeline, "process_part", fake)
    result = pipeline.process_pdf(pdf, tmp_path / "out", _NoAI(), generate_html=False)
    assert len(set(ids)) == 2 and len(result.specs) == 2, ids
    assert [r.status for r in result.reports] == ["NIEPEWNE", "NIEPEWNE"]


def test_same_drawing_number_gives_separate_step_names(tmp_path):
    from drawing2cad.pipeline import process_part

    pdf = tmp_path / "two.pdf"
    _two_sheets(pdf)
    a, b = extract_pdf(pdf).parts
    ra, _ = process_part(a.model_copy(update={"warnings": ["x"]}), _NoAI(), tmp_path, part_id="PART-001_s1")
    assert ra.drawing_no == "PART-001_s1"


# --- 15. negative deviation with a decimal point -----------------------------------------------

@pytest.mark.parametrize(("text", "value", "tol"), [("10-0.1", 10.0, "-0.1"), ("25-0.05", 25.0, "-0.05"),
                                                    ("10-0,1", 10.0, "-0,1")])
def test_dot_decimal_negative_deviation_is_a_dimension(text, value, tol):
    d = parse_dimension_token(_c(text), "D1")
    assert d is not None and (d.value, d.tolerance) == (value, tol)


# --- 16. safety nets in verification and in the AI prompt --------------------------------------

def test_uncertain_dimension_type_is_never_ok():
    from drawing2cad.blocks import build_part
    from drawing2cad.drawing_compare import extract_views, parse_scale
    from drawing2cad.schema import PartSpec
    from drawing2cad.verifier import verify_part

    pdf = REPO_ROOT / "rysunki" / "cad_07_1.pdf"
    part = next(p for p in extract_pdf(pdf).parts if p.drawing_no == "01.01.02")
    spec = PartSpec.model_validate_json(
        (REPO_ROOT / "tests" / "fixtures" / "reczne" / "01.01.02.json").read_text(encoding="utf-8"))
    views = extract_views(str(pdf), part.page_index, part.bbox)
    scale = parse_scale(part.title_block.skala) if part.title_block else None
    assert verify_part(spec, build_part(spec), part.dimensions, views=views, sheet_scale=scale).status == "OK"
    dims = list(part.dimensions)
    i = next(k for k, d in enumerate(dims) if d.dim_type == "dlugosc")
    dims[i] = dims[i].model_copy(update={"type_uncertain": True})
    report = verify_part(spec, build_part(spec), dims, views=views, sheet_scale=scale)
    assert report.status != "OK"
    assert any(a.id == "PROG-RODZAJ-WYMIARU-NIEPEWNY" for a in report.ambiguities)


def test_prompt_tells_the_ai_about_an_uncertain_type():
    from drawing2cad.interpreter import build_user_prompt
    from drawing2cad.schema import ExtractedDimension, PartRegion

    d = ExtractedDimension(id="D1", raw_text="12", decoded_text="12", dim_type="dlugosc", value=12.0, bbox=(0, 0, 1, 1))
    part = PartRegion(page_index=0, bbox=(0, 0, 1, 1),
                      dimensions=[d, d.model_copy(update={"id": "D2", "type_uncertain": True})])
    prompt = build_user_prompt(part)
    one, two = prompt.split('"id": "D2"')
    assert "type_uncertain" not in one and '"type_uncertain": true' in two


def test_part_warnings_end_in_error_through_the_whole_pipeline(tmp_path):
    from drawing2cad.pipeline import process_pdf

    result = process_pdf(NIST / "ftc" / "nist_ftc_07_asme1_rd.pdf", tmp_path, _NoAI(), generate_html=False)
    assert result.reports and all(r.status == "BŁĄD" for r in result.reports)
