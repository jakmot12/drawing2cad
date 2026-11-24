"""Regression tests for the third external review (N1-N9) and real run 7."""
from __future__ import annotations

from pathlib import Path

import cadquery as cq
import pytest

from drawing2cad.blocks import build_part
from drawing2cad.drawing_compare import compare_views, extract_views
from drawing2cad.pdf_extract import extract_pdf
from drawing2cad.schema import ExtractedDimension, PartSpec
from drawing2cad.verifier import verify_part

from sheet_helpers import (
    FRONT,
    LEFT_VIEW_PLACED_RIGHT,
    RIGHT_VIEW_PLACED_LEFT,
    TOP_VIEW_PLACED_BELOW,
    draw_sheet,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RECZNE = REPO_ROOT / "tests" / "fixtures" / "reczne"
WHOLE_PAGE = (0, 0, 595, 842)


def _dim(i, text, dtype, value, mult=1, secondary=None):
    return ExtractedDimension(id=f"D{i}", raw_text=text, decoded_text=text, dim_type=dtype, value=value,
                              multiplicity=mult, secondary_value=secondary, bbox=(0, 0, 1, 1))


def _corner_block(mirror: bool = False):
    x = -16.0 if mirror else 16.0
    return cq.Workplane("XY").box(40, 20, 10).cut(cq.Workplane("XY").box(8, 8, 5).translate((x, 6, 2.5)))


def _steps_bar(mirror: bool = False):
    """Chiral bar from the review: steps of 2 mm (-Y side) and 6 mm (+Y side) at z = 6, hole along Y."""
    s = -1.0 if mirror else 1.0
    bar = cq.Workplane("XY").box(40, 20, 10)
    bar = bar.cut(cq.Workplane("XY").box(40, 2, 4).translate((0, s * -9.0, 3.0)))
    bar = bar.cut(cq.Workplane("XY").box(40, 6, 4).translate((0, s * 7.0, 3.0)))
    return bar.cut(cq.Workplane("XZ").center(-10, -2).circle(2).extrude(15, both=True))


# ---------------------------------------------------------------- N1: three views in one row

def test_three_views_in_a_row_are_not_a_false_alarm(tmp_path):
    pdf = tmp_path / "row.pdf"
    draw_sheet(_corner_block(), pdf, [(RIGHT_VIEW_PLACED_LEFT, (100.0, 300.0)), (FRONT, (250.0, 300.0)),
                                      (LEFT_VIEW_PLACED_RIGHT, (420.0, 300.0))])
    views = extract_views(str(pdf), 0, WHOLE_PAGE)
    assert len(views) == 3
    res = compare_views(_corner_block().val().wrapped, views, [], 1.0)
    assert all(r.ok for r in res), [r.message() for r in res]


# ---------------------------------------------------------------- N3: two-view mirror

def test_two_view_mirror_is_not_ok(tmp_path):
    pdf = tmp_path / "bar.pdf"
    draw_sheet(_steps_bar(), pdf, [(FRONT, (200.0, 300.0)), (LEFT_VIEW_PLACED_RIGHT, (380.0, 300.0))])
    views = extract_views(str(pdf), 0, WHOLE_PAGE)
    assert len(views) == 2
    good = compare_views(_steps_bar().val().wrapped, views, [], 1.0)
    assert all(r.ok for r in good), [r.message() for r in good]
    mirrored = compare_views(_steps_bar(mirror=True).val().wrapped, views, [], 1.0)
    assert not all(r.ok for r in mirrored), [r.message() for r in mirrored]


# ---------------------------------------------------------------- N6: frame styles

@pytest.mark.parametrize(("frame", "frame_w"), [("rect", 1.44), ("lines", 1.98), ("lines", 1.44)])
def test_frame_width_or_shape_does_not_hide_views(tmp_path, frame, frame_w):
    pdf = tmp_path / "frame.pdf"
    draw_sheet(_corner_block(), pdf, [(FRONT, (200.0, 300.0)), (TOP_VIEW_PLACED_BELOW, (200.0, 450.0))],
               frame=frame, frame_w=frame_w)
    views = extract_views(str(pdf), 0, WHOLE_PAGE)
    assert len(views) == 2
    res = compare_views(_corner_block().val().wrapped, views, [], 1.0)
    assert all(r.ok for r in res), [r.message() for r in res]


# ---------------------------------------------------------------- N7: chamfers at 30 degrees

def test_planar_chamfer_1x30_is_measured():
    spec = PartSpec.model_validate({
        "drawing_no": "T5", "name": "Płytka", "part_type": "plaska",
        "operations": [
            {"op": "plyta_z_profilu", "plane": "XY", "thickness": 6.0,
             "shapes": [{"kind": "rect", "cx": 0, "cy": 0, "width": 40, "height": 20}]},
            {"op": "fazka", "size": 1.0, "angle_deg": 30.0, "face_selectors": [">Z"]},
        ],
        "dimension_bindings": [{"dim_id": "D1", "target": "fazka"}],
    })
    report = verify_part(spec, build_part(spec), [_dim(1, "1x30°", "fazka", 1.0, secondary=30.0)])
    assert not any("Fazka" in e or "fazek" in e for e in report.errors), report.errors


def test_conical_chamfer_1x30_is_measured():
    spec = PartSpec.model_validate({
        "drawing_no": "T6", "name": "Sworzeń", "part_type": "toczona",
        "operations": [
            {"op": "walec", "axis": "Z", "diameter": 10, "length": 30, "offset": 0},
            {"op": "fazka", "size": 1.0, "angle_deg": 30.0, "face_selectors": [">Z"]},
        ],
        "dimension_bindings": [{"dim_id": "D1", "target": "fazka"}],
    })
    report = verify_part(spec, build_part(spec), [_dim(1, "1x30°", "fazka", 1.0, secondary=30.0)])
    assert not any("Fazka" in e or "fazek" in e for e in report.errors), report.errors


# ---------------------------------------------------------------- N4: one-wall hole, blind hole with depth

def _channel_with_hole(depth):
    return PartSpec.model_validate({
        "drawing_no": "T7", "name": "Ceownik", "part_type": "blacha_gieta",
        "operations": [
            {"op": "ceownik", "length": 50, "outer_width": 30, "height": 20, "wall_thickness": 3, "web_thickness": 3},
            {"op": "otwor", "plane": "XZ", "diameter": 6, "positions": [[0, 8]], "depth": depth},
        ],
    })


def test_hole_through_one_wall_is_uncertain_not_error():
    spec = _channel_with_hole(15.0)  # from y=0 towards -y: through the -y wall only
    report = verify_part(spec, build_part(spec), [])
    assert not any("kończy się w materiale" in e for e in report.errors)
    assert any(a.id == "PROG-OTWOR-CZESCIOWY" for a in report.ambiguities)


def test_blind_hole_needs_a_depth_dimension():
    spec = PartSpec.model_validate({
        "drawing_no": "T8", "name": "Płytka", "part_type": "plaska",
        "operations": [
            {"op": "plyta_z_profilu", "plane": "XY", "thickness": 10,
             "shapes": [{"kind": "rect", "cx": 0, "cy": 0, "width": 30, "height": 30}]},
            {"op": "otwor", "plane": "XY", "diameter": 6, "positions": [[0, 0]], "depth": 5},
        ],
    })
    no_dim = verify_part(spec, build_part(spec), [])
    assert any("kończy się w materiale" in e for e in no_dim.errors)
    spec.dimension_bindings = [{"dim_id": "D1", "target": "glebokosc_otworu"}]
    spec = PartSpec.model_validate(spec.model_dump())
    with_dim = verify_part(spec, build_part(spec), [_dim(1, "5", "dlugosc", 5.0)])
    assert not any("kończy się w materiale" in e for e in with_dim.errors), with_dim.errors


# ---------------------------------------------------------------- N9 + run 7: thread counting

@pytest.fixture(scope="module")
def oska():
    return next(p for p in extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf").parts if p.drawing_no == "01.01.04")


def test_two_coaxial_pins_count_as_two_threads(oska):
    """Run 7: the AI wrote multiplicity 2 for the M10 pins; two pins on one axis are two threads."""
    spec = PartSpec.model_validate_json((RECZNE / "01.01.04.json").read_text(encoding="utf-8"))
    for b in spec.dimension_bindings:
        if b.target == "gwint_zewnetrzny":
            b.multiplicity = 2
    report = verify_part(spec, build_part(spec), oska.dimensions)
    assert report.passed, report.errors


def test_two_internal_thread_labels_need_two_holes(oska):
    spec = PartSpec.model_validate_json((RECZNE / "01.01.04.json").read_text(encoding="utf-8"))
    internal = next(b for b in spec.dimension_bindings if b.target == "gwint_wewnetrzny")
    external = next(b for b in spec.dimension_bindings if b.target == "gwint_zewnetrzny")
    external.target = "gwint_wewnetrzny"  # both M10 labels claimed by the single cross hole
    report = verify_part(spec, build_part(spec), oska.dimensions)
    assert report.status == "BŁĄD"
    assert internal.dim_id
