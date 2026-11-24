"""Verifier tests: dimension checklist + comparison with the drawn views."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from drawing2cad.blocks import build_part
from drawing2cad.drawing_compare import extract_views, parse_scale
from drawing2cad.pdf_extract import extract_pdf
from drawing2cad.schema import PartSpec
from drawing2cad.verifier import verify_part

REPO_ROOT = Path(__file__).resolve().parent.parent
PDF = REPO_ROOT / "rysunki" / "cad_07_1.pdf"
RECZNE = REPO_ROOT / "tests" / "fixtures" / "reczne"


@pytest.fixture(scope="module")
def parts():
    out = {}
    for p in extract_pdf(PDF).parts:
        views = extract_views(str(PDF), p.page_index, p.bbox)
        scale = parse_scale(p.title_block.skala) if p.title_block else None
        out[p.drawing_no] = (p, views, scale)
    return out


def _load_spec(drawing_no: str) -> PartSpec:
    return PartSpec.model_validate_json((RECZNE / f"{drawing_no}.json").read_text(encoding="utf-8"))


def _verify(parts, drawing_no: str, spec: PartSpec, with_views: bool = True):
    part, views, scale = parts[drawing_no]
    return verify_part(spec, build_part(spec), part.dimensions,
                       views=views if with_views else None, sheet_scale=scale)


@pytest.mark.parametrize(
    ("drawing_no", "expected_status"),
    [
        ("01.01.01", "NIEPEWNE"),  # hole position not dimensioned
        ("01.01.02", "OK"),
        ("01.01.03", "NIEPEWNE"),  # R1 not shown edge-on in any view (program) + manual spec remark
        ("01.01.04", "OK"),
        ("01.01.05", "OK"),
        ("01.01.06", "OK"),        # web-length remark settled: only a 125 mm web matches all 3 views
    ],
)
def test_correct_parts_pass_dimensions_and_views(parts, drawing_no, expected_status):
    report = _verify(parts, drawing_no, _load_spec(drawing_no))
    assert report.passed, f"{drawing_no}: {report.errors}"
    assert report.status == expected_status
    assert report.unused_dimensions == []
    drawn = [v for v in report.view_checks if v.view_index > 0]
    assert drawn and all(v.ok for v in drawn)
    # whole-model notes may only be "not decidable from the drawing", never an error
    assert all(v.uncertain for v in report.view_checks if v.view_index == 0)


def test_without_drawn_views_result_is_never_ok(parts):
    report = _verify(parts, "01.01.02", _load_spec("01.01.02"), with_views=False)
    assert report.passed
    assert report.status == "NIEPEWNE"
    assert any(a.id == "BRAK-POROWNANIA-Z-RYSUNKIEM" for a in report.ambiguities)


def test_wrong_hole_diameter(parts):
    bad = _load_spec("01.01.02")
    bad.operations[1].diameter = 12.0  # type: ignore[union-attr]
    report = _verify(parts, "01.01.02", bad)
    assert report.status == "BŁĄD"
    assert any("Ø10" in e and "12" in e for e in report.errors)


def test_wrong_hole_spacing(parts):
    bad = _load_spec("01.01.06")
    bad.operations[1].positions = [(-60.0, 12.0), (60.0, 12.0)]  # type: ignore[union-attr]
    report = _verify(parts, "01.01.06", bad)
    assert report.status == "BŁĄD"
    assert any("125" in e for e in report.errors)


def test_unused_pdf_dimension(parts):
    bad = _load_spec("01.01.02")
    bad.dimension_bindings = bad.dimension_bindings[:-1]
    report = _verify(parts, "01.01.02", bad)
    assert report.status == "BŁĄD"
    assert len(report.unused_dimensions) == 1


def test_rule_1_no_number_tampering(parts):
    bad = _load_spec("01.01.02")
    for b in bad.dimension_bindings:
        if b.dim_id == "D12":
            b.expected_value = 99.0
    report = _verify(parts, "01.01.02", bad)
    assert report.status == "BŁĄD"
    assert any("zasady 1" in e for e in report.errors)


def test_dimension_type_must_match_target(parts):
    """A diameter from the drawing cannot be 'checked' against a corner radius."""
    bad = _load_spec("01.01.01")
    for b in bad.dimension_bindings:
        if b.target == "otwor_srednica":
            b.target = "promien"
    report = _verify(parts, "01.01.01", bad)
    assert report.status == "BŁĄD"
    assert any("nie może być sprawdzany jako 'promien'" in e for e in report.errors)


def test_multiplicity_comes_from_the_drawing(parts):
    """AI writing multiplicity=1 for '2xØ10' must still require two holes."""
    bad = _load_spec("01.01.01")
    for op in bad.operations:
        if op.op == "otwor":
            op.positions = [(-15.0, 10.0)]
    for b in bad.dimension_bindings:
        if b.target == "otwor_srednica":
            b.multiplicity = 1
    report = _verify(parts, "01.01.01", bad, with_views=False)
    assert report.status == "BŁĄD"
    assert any("2x otwór Ø10" in e for e in report.errors)


def test_one_wall_cannot_satisfy_two_thickness_labels(parts):
    bad = _load_spec("01.01.02")
    bad.operations[0].wall_thickness = 8.0  # type: ignore[union-attr]
    report = _verify(parts, "01.01.02", bad, with_views=False)  # numbers alone must catch it
    assert report.status == "BŁĄD"
    assert any("ścianek grubości 3" in e for e in report.errors)


def test_wrong_thread_direction(parts):
    bad = _load_spec("01.01.05")
    for th in bad.threads:
        th.direction = "prawy"
    report = _verify(parts, "01.01.05", bad)
    assert report.status == "BŁĄD"
    assert any("lewy" in e for e in report.errors)


def test_tapped_hole_at_minor_diameter_is_accepted(parts):
    """A tapped M10 hole may be modelled at the tap drill diameter (Inventor style)."""
    spec = _load_spec("01.01.04")
    spec.operations[-1].diameter = 8.5  # type: ignore[union-attr]
    report = _verify(parts, "01.01.04", spec)
    assert report.passed, report.errors


def test_slightly_wrong_diameter_is_not_tolerated(parts):
    bad = _load_spec("01.01.02")
    bad.operations[1].diameter = 10.14  # type: ignore[union-attr]
    report = _verify(parts, "01.01.02", bad, with_views=False)
    assert report.status == "BŁĄD"


@pytest.mark.parametrize("drawing_no", ["01.01.01", "01.01.02", "01.01.03", "01.01.04", "01.01.05", "01.01.06"])
def test_program_flags_undimensioned_hole_positions_without_ai_help(parts, drawing_no):
    silent = _load_spec(drawing_no)
    silent.ambiguities = []  # the AI keeps quiet
    report = _verify(parts, drawing_no, silent)
    prog = [a for a in report.ambiguities if a.id == "PROG-POLOZENIE-OTWOROW"]
    if drawing_no == "01.01.01":
        assert report.status == "NIEPEWNE" and len(prog) == 1
    else:
        # dimensioned (Podstawa), concentric with an end arc (Ramię, Dźwignia's square hole),
        # centred cross hole (Ośka), or no holes (Śruba)
        assert prog == [], prog


def test_program_reports_hole_position_even_when_ai_does(parts):
    """The program's own finding must not depend on the AI having said the same thing
    (otherwise settling the AI's remark would hide a real drawing ambiguity)."""
    report = _verify(parts, "01.01.01", _load_spec("01.01.01"))
    assert any(a.id == "PROG-POLOZENIE-OTWOROW" and a.source == "program" for a in report.ambiguities)
    assert report.status == "NIEPEWNE"
