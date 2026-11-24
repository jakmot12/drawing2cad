"""Regression tests for the fourth external review (R4-1 ... R4-8)."""
from __future__ import annotations

import cadquery as cq
import pymupdf

from drawing2cad.blocks import build_part
from drawing2cad.drawing_compare import compare_views, extract_views
from drawing2cad.schema import ExtractedDimension, PartSpec
from drawing2cad.verifier import verify_part

from sheet_helpers import FRONT, LEFT_VIEW_PLACED_RIGHT, TOP_VIEW_PLACED_BELOW, draw_sheet

WHOLE_PAGE = (0, 0, 595, 842)


def _dim(i, text, dtype, value):
    return ExtractedDimension(id=f"D{i}", raw_text=text, decoded_text=text, dim_type=dtype, value=value, bbox=(0, 0, 1, 1))


def _corner_block():
    return cq.Workplane("XY").box(40, 20, 10).cut(cq.Workplane("XY").box(8, 8, 5).translate((16, 6, 2.5)))


def test_r4_1_neighbouring_views_with_a_small_gap_stay_separate(tmp_path):
    pdf = tmp_path / "gap.pdf"
    # front is 40 mm (113 pt) wide around x=200; the left view starts ~25 pt to its right
    draw_sheet(_corner_block(), pdf, [(FRONT, (200.0, 300.0)), (LEFT_VIEW_PLACED_RIGHT, (310.0, 300.0))])
    views = extract_views(str(pdf), 0, WHOLE_PAGE)
    assert len(views) == 2 and not any(v.is_broken for v in views)


def test_r4_2_thick_section_marks_do_not_steal_the_visible_width(tmp_path):
    pdf = tmp_path / "marks.pdf"
    # front + top: together they show the corner notch (front + left would hide it entirely)
    draw_sheet(_corner_block(), pdf, [(FRONT, (200.0, 300.0)), (TOP_VIEW_PLACED_BELOW, (200.0, 450.0))])
    doc = pymupdf.open(str(pdf))
    page = doc[0]
    for x in (150, 450):  # two short, very thick section-plane marks
        page.draw_line((x, 200), (x, 212), color=(0, 0, 0), width=2.88)
    out = tmp_path / "marks2.pdf"
    doc.save(str(out))
    views = extract_views(str(out), 0, WHOLE_PAGE)
    assert len(views) == 2
    res = compare_views(_corner_block().val().wrapped, views, [], 1.0)
    assert all(r.ok for r in res), [r.message() for r in res]


def test_r4_4_dimensioned_hole_near_the_centre_is_no_symmetry_error():
    spec = PartSpec.model_validate({
        "drawing_no": "T9", "name": "Płytka", "part_type": "plaska",
        "operations": [
            {"op": "plyta_z_profilu", "plane": "XY", "thickness": 6,
             "shapes": [{"kind": "rect", "cx": 0, "cy": 0, "width": 40, "height": 20}]},
            {"op": "otwor", "plane": "XY", "diameter": 6, "positions": [[0, 0.2]]},
        ],
        "dimension_bindings": [{"dim_id": "D1", "target": "otwor_wysokosc", "expected_value": 10.2},
                               {"dim_id": "D2", "target": "otwor_srednica"}],
    })
    report = verify_part(spec, build_part(spec), [_dim(1, "10,2", "dlugosc", 10.2), _dim(2, "Ø6", "srednica", 6.0)])
    assert not any("osi symetrii" in e for e in report.errors), report.errors


def test_r4_5_third_angle_drawing_is_uncertain_not_error(tmp_path):
    pdf = tmp_path / "us.pdf"
    draw_sheet(_corner_block(), pdf, [(FRONT, (200.0, 300.0)), (("+X", 0), (380.0, 300.0))])
    views = extract_views(str(pdf), 0, WHOLE_PAGE)
    res = compare_views(_corner_block().val().wrapped, views, [], 1.0)
    assert all(r.uncertain and not r.ok for r in res), [r.message() for r in res]
    assert any("amerykańskim" in r.message() for r in res)


def test_r4_3_undocumented_hole_is_an_error():
    spec = PartSpec.model_validate({
        "drawing_no": "T10", "name": "Płytka", "part_type": "plaska",
        "operations": [
            {"op": "plyta_z_profilu", "plane": "XY", "thickness": 6,
             "shapes": [{"kind": "rect", "cx": 0, "cy": 0, "width": 40, "height": 20}]},
            {"op": "otwor", "plane": "XY", "diameter": 6, "positions": [[-10, 0]]},
            {"op": "otwor", "plane": "XY", "diameter": 4, "positions": [[10, 0]]},
        ],
        "dimension_bindings": [{"dim_id": "D1", "target": "otwor_srednica"}],
    })
    report = verify_part(spec, build_part(spec), [_dim(1, "Ø6", "srednica", 6.0)])
    assert any("Ø4" in e and "nie odpowiada" in e for e in report.errors), report.errors


def test_r4_8_square_hole_through_one_wall_is_uncertain():
    spec = PartSpec.model_validate({
        "drawing_no": "T11", "name": "Ceownik", "part_type": "blacha_gieta",
        "operations": [
            {"op": "ceownik", "length": 50, "outer_width": 30, "height": 20, "wall_thickness": 3, "web_thickness": 3},
            {"op": "otwor_kwadratowy", "plane": "XZ", "size": 6, "positions": [[0, 8]], "depth": 15},
        ],
    })
    report = verify_part(spec, build_part(spec), [])
    assert not any("□6" in e for e in report.errors), report.errors
    assert any(a.id == "PROG-OTWOR-CZESCIOWY" for a in report.ambiguities)
