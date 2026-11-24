"""Regression tests for the second external review (findings B1-B9, Z5, Z12)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import cadquery as cq
import numpy as np
import pymupdf
import pytest

from drawing2cad.blocks import build_part, execute_operation
from drawing2cad.drawing_compare import (
    PT_TO_MM,
    compare_views,
    extract_views,
    project_model_edges,
    view_frame,
)
from drawing2cad.interpreter import FatalInterpreterError, LLMInterpreter
from drawing2cad.pdf_extract import extract_pdf
from drawing2cad.schema import ExtractedDimension, PartSpec, WalecOp
from drawing2cad.verifier import verify_part

REPO_ROOT = Path(__file__).resolve().parent.parent
RECZNE = REPO_ROOT / "tests" / "fixtures" / "reczne"


def _dim(i: int, text: str, dtype: str, value: float, mult: int = 1, secondary=None, thread=None):
    return ExtractedDimension(id=f"D{i}", raw_text=text, decoded_text=text, dim_type=dtype, value=value,
                              multiplicity=mult, secondary_value=secondary, thread_spec=thread,
                              bbox=(0, 0, 1, 1))


# ---------------------------------------------------------------- B1 / NC6: mirrored chiral part

def _chiral(mirror: bool) -> cq.Workplane:
    """40 x 20 x 10 block with one corner notch: no mirror symmetry at all."""
    x = -16.0 if mirror else 16.0
    return cq.Workplane("XY").box(40, 20, 10).cut(cq.Workplane("XY").box(8, 8, 5).translate((x, 6, 2.5)))


def _draw_first_angle(solid: cq.Workplane, path: Path) -> None:
    """Sheet at 1:1 in first-angle projection: front (looking +Y), left view placed right,
    top view placed below. Visible edges 1.44 pt, a few thin centre lines 0.72 pt."""
    edges = project_model_edges(solid.val().wrapped)
    layout = {  # view name, rotation, sheet origin of the model origin [pt]
        "front": ("-Y", 3, (150.0, 250.0)),
        "left": ("-X", 0, (350.0, 250.0)),
        "top": ("+Z", 0, (150.0, 450.0)),
    }
    rot = {0: np.eye(2), 3: np.array([[0, 1], [-1, 0]], float)}
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for name, r, (ox, oy) in layout.values():
        for e in edges[name]:
            ab = e @ rot[r].T
            pts = [(ox + a / PT_TO_MM, oy - b / PT_TO_MM) for a, b in ab]
            page.draw_polyline(pts, color=(0, 0, 0), width=1.44)
    for y in (100, 110, 120):  # thin lines so the page has two line widths
        page.draw_line((20, y), (60, y), color=(0, 0, 0), width=0.72)
    doc.save(str(path))


def test_layout_frames_follow_first_angle():
    nf, af, bf = view_frame("-Y", 3)
    nl, al, bl = view_frame("-X", 0)
    nt, at, bt = view_frame("+Z", 0)
    assert (af, bf) == ((1, 0, 0), (0, 0, 1))
    assert bl == bf and nl == tuple(-v for v in af)  # left view placed right
    assert at == af and nt == bf  # top view placed below


def test_mirrored_chiral_part_is_rejected(tmp_path):
    pdf = tmp_path / "chiral.pdf"
    _draw_first_angle(_chiral(mirror=False), pdf)
    views = extract_views(str(pdf), 0, (0, 0, 595, 842))
    assert len(views) == 3

    good = compare_views(_chiral(mirror=False).val().wrapped, views, [], 1.0)
    assert all(v.ok for v in good), [v.message() for v in good]

    mirrored = compare_views(_chiral(mirror=True).val().wrapped, views, [], 1.0)
    assert not all(v.ok for v in mirrored)
    assert not any(v.uncertain for v in mirrored)  # an error, not a shrug


# ---------------------------------------------------------------- B6: planar chamfer

def test_planar_chamfer_is_measured():
    spec = PartSpec.model_validate({
        "drawing_no": "T1", "name": "Płytka", "part_type": "plaska",
        "operations": [
            {"op": "plyta_z_profilu", "plane": "XY", "thickness": 6.0,
             "shapes": [{"kind": "rect", "cx": 0, "cy": 0, "width": 40, "height": 20}]},
            {"op": "fazka", "size": 1.0, "angle_deg": 45.0, "face_selectors": [">Z"]},
        ],
        "dimension_bindings": [
            {"dim_id": "D1", "target": "gabaryt"}, {"dim_id": "D2", "target": "gabaryt"},
            {"dim_id": "D3", "target": "grubosc"}, {"dim_id": "D4", "target": "fazka"},
        ],
    })
    dims = [_dim(1, "40", "dlugosc", 40), _dim(2, "20", "dlugosc", 20), _dim(3, "6", "dlugosc", 6),
            _dim(4, "1x45°", "fazka", 1.0, secondary=45.0)]
    report = verify_part(spec, build_part(spec), dims)
    assert report.passed, report.errors


# ---------------------------------------------------------------- B7: square needs perpendicular pairs

def test_two_parallel_walls_are_not_a_square():
    spec = PartSpec.model_validate({
        "drawing_no": "T2", "name": "Ceownik", "part_type": "blacha_gieta",
        "operations": [{"op": "ceownik", "length": 50, "outer_width": 30, "height": 20,
                        "wall_thickness": 7, "web_thickness": 3}],
        "dimension_bindings": [{"dim_id": "D1", "target": "kwadrat"}],
    })
    report = verify_part(spec, build_part(spec), [_dim(1, "□7", "kwadrat", 7.0)])
    assert report.status == "BŁĄD"
    assert any("prostopadłe" in e for e in report.errors)


# ---------------------------------------------------------------- B7: thread lengths 1-to-1

@pytest.fixture(scope="module")
def sruba():
    return next(p for p in extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf").parts if p.drawing_no == "01.01.05")


def test_thread_length_without_declared_length_is_not_accepted(sruba):
    spec = PartSpec.model_validate_json((RECZNE / "01.01.05.json").read_text(encoding="utf-8"))
    spec.operations[0].length = 100.0  # type: ignore[union-attr]
    spec.operations[1].offset = 100.0  # type: ignore[union-attr]
    spec.operations[1].length = 210.0  # type: ignore[union-attr]
    spec.threads[1].length = None
    report = verify_part(spec, build_part(spec), sruba.dimensions)
    assert report.status == "BŁĄD"


def test_one_thread_entry_cannot_serve_two_length_dimensions(sruba):
    spec = PartSpec.model_validate_json((RECZNE / "01.01.05.json").read_text(encoding="utf-8"))
    spec.threads = spec.threads[:1] + [spec.threads[1].model_copy(update={"length": 140.0})]
    report = verify_part(spec, build_part(spec), sruba.dimensions)
    assert report.status == "BŁĄD"


# ---------------------------------------------------------------- B2/B7: 2xM10 needs two holes

def test_thread_multiplicity_from_label():
    oska = next(p for p in extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf").parts if p.drawing_no == "01.01.04")
    spec = PartSpec.model_validate_json((RECZNE / "01.01.04.json").read_text(encoding="utf-8"))
    internal = next(b.dim_id for b in spec.dimension_bindings if b.target == "gwint_wewnetrzny")
    dims = [d.model_copy(update={"multiplicity": 2}) if d.id == internal else d for d in oska.dimensions]
    report = verify_part(spec, build_part(spec), dims)
    assert report.status == "BŁĄD"
    assert any("2xM10" in e for e in report.errors)


# ---------------------------------------------------------------- voids and loose bodies

def test_internal_void_and_two_bodies_are_errors():
    spec = PartSpec.model_validate({
        "drawing_no": "T3", "name": "Kostka", "part_type": "plaska",
        "operations": [{"op": "plyta_z_profilu", "plane": "XY", "thickness": 10,
                        "shapes": [{"kind": "rect", "cx": 0, "cy": 0, "width": 20, "height": 20}]}],
    })
    solid = build_part(spec)
    void = solid.cut(cq.Workplane("XY").box(4, 4, 4).translate((0, 0, 5)))
    assert any("puste przestrzenie" in e for e in verify_part(spec, void, []).errors)
    two = solid.union(cq.Workplane("XY").box(2, 2, 2).translate((30, 0, 0)))
    assert any("osobnych brył" in e for e in verify_part(spec, two, []).errors)


# ---------------------------------------------------------------- B8: HTTP errors are not retried

def test_http_auth_error_is_fatal(tmp_path):
    import openai

    part = next(p for p in extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf").parts if p.drawing_no == "01.01.02")
    interp = LLMInterpreter(provider="deepseek", model="m", api_key="bad", vision=False, cache_dir=tmp_path)

    def boom(**_):
        # built without the SDK's HTTP response object (its HTTP library changes between versions)
        exc = openai.AuthenticationError.__new__(openai.AuthenticationError)
        Exception.__init__(exc, "invalid key")
        raise exc

    interp._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
    with pytest.raises(FatalInterpreterError, match="DEEPSEEK_API_KEY"):
        interp.interpret(part)


# ---------------------------------------------------------------- Z12: axis Y like X and Z

@pytest.mark.parametrize("axis", ["X", "Y", "Z"])
def test_cylinder_runs_along_positive_axis(axis):
    s = execute_operation(None, WalecOp(axis=axis, diameter=4, length=10, offset=5)).val().BoundingBox()
    lo = {"X": s.xmin, "Y": s.ymin, "Z": s.zmin}[axis]
    hi = {"X": s.xmax, "Y": s.ymax, "Z": s.zmax}[axis]
    assert (round(lo, 6), round(hi, 6)) == (5.0, 15.0)


# ---------------------------------------------------------------- Z5: disc (D > L) is no false alarm

def test_turned_disc_with_diameter_larger_than_length():
    spec = PartSpec.model_validate({
        "drawing_no": "T4", "name": "Tarcza", "part_type": "toczona",
        "operations": [
            {"op": "walec", "axis": "Z", "diameter": 50, "length": 10, "offset": 0},
            {"op": "walec", "axis": "Z", "diameter": 20, "length": 5, "offset": 10},
        ],
        "dimension_bindings": [
            {"dim_id": "D1", "target": "walec_srednica"}, {"dim_id": "D2", "target": "walec_srednica"},
            {"dim_id": "D3", "target": "dlugosc_odcinka"}, {"dim_id": "D4", "target": "dlugosc_odcinka"},
            {"dim_id": "D5", "target": "gabaryt"},
        ],
    })
    dims = [_dim(1, "Ø50", "srednica", 50), _dim(2, "Ø20", "srednica", 20), _dim(3, "10", "dlugosc", 10),
            _dim(4, "5", "dlugosc", 5), _dim(5, "15", "dlugosc", 15)]
    report = verify_part(spec, build_part(spec), dims)
    assert report.passed, report.errors
    assert report.status == "NIEPEWNE"  # no drawn views given


def test_http_402_insufficient_balance_is_fatal(tmp_path):
    """Real run 9: DeepSeek answered 402 Insufficient Balance; retrying cannot help."""
    import openai

    part = next(p for p in extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf").parts if p.drawing_no == "01.01.02")
    interp = LLMInterpreter(provider="deepseek", model="m", api_key="k", vision=False, cache_dir=tmp_path)

    def boom(**_):
        exc = openai.APIStatusError.__new__(openai.APIStatusError)
        Exception.__init__(exc, "Error code: 402 - Insufficient Balance")
        exc.status_code = 402
        raise exc

    interp._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
    with pytest.raises(FatalInterpreterError, match="środków"):
        interp.interpret(part)
