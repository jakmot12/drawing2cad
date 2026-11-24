"""Dimension texts beyond the jack drawings: tolerances, fits, threads, multiplicity, reference.
Nothing dimension-like may disappear silently."""
from __future__ import annotations

from pathlib import Path

import pytest

from drawing2cad.interpreter import ManualSpecInterpreter, build_user_prompt
from drawing2cad.pdf_extract import _looks_like_dimension, _parse_region_dimensions, _TextCluster, extract_pdf, parse_dimension_token
from drawing2cad.pipeline import process_part

REPO_ROOT = Path(__file__).resolve().parent.parent


def _c(text: str, x: float = 0.0, y: float = 0.0) -> _TextCluster:
    return _TextCluster(raw_text=text, decoded_text=text, bbox=(x, y, x + 10, y + 5), direction=(1.0, 0.0), size=10.0)


@pytest.mark.parametrize(
    ("text", "dtype", "value", "mult", "tol", "extra"),
    [
        ("Ø10H7", "srednica", 10.0, 1, "H7", None),
        ("Ø10 H7/g6", "srednica", 10.0, 1, "H7/g6", None),
        ("25±0,1", "dlugosc", 25.0, 1, "±0,1", None),
        ("50 +0,1/-0,2", "dlugosc", 50.0, 1, "+0,1/-0,2", None),
        ("R5 max", "promien", 5.0, 1, "max", None),
        ("10h6", "dlugosc", 10.0, 1, "h6", None),
        ("4x Ø10", "srednica", 10.0, 4, None, None),
        ("Ø10 (4x)", "srednica", 10.0, 4, None, None),
        ("4 otw. Ø6,6", "srednica", 6.6, 4, None, None),
        ("M10x1,5", "gwint", 10.0, 1, None, "M10x1.5"),
        ("M12-6g", "gwint", 12.0, 1, None, "M12-6g"),
        ("2xM8", "gwint", 8.0, 2, None, "M8"),
        ("(25)", "dlugosc", 25.0, 1, None, "ref"),
        ("1x45°", "fazka", 1.0, 1, None, None),
        ("2xØ10", "srednica", 10.0, 2, None, None),
        ("M10 lewy", "gwint", 10.0, 1, None, "M10 LH"),
    ],
)
def test_dimension_forms(text, dtype, value, mult, tol, extra):
    d = parse_dimension_token(_c(text), "D1")
    assert d is not None, text
    assert (d.dim_type, d.value, d.multiplicity, d.tolerance) == (dtype, value, mult, tol)
    assert d.decoded_text == text
    if extra == "ref":
        assert d.reference
    elif extra:
        assert d.thread_spec == extra


def test_stacked_tolerances_attach_to_the_nearest_dimension():
    dims, unknown = _parse_region_dimensions([_c("50", 100, 100), _c("+0,1", 115, 97), _c("-0,2", 115, 104)])
    assert unknown == []
    assert dims[0].tolerance == "+0,1/-0,2"


def test_dimension_like_text_that_cannot_be_parsed_is_reported():
    dims, unknown = _parse_region_dimensions([_c("SR12"), _c("Ø10 g"), _c("Uwagi: krawędzie stępić 0,5")])
    assert "SR12" in unknown and "Ø10 g" in unknown
    assert not any("Uwagi" in u for u in unknown)  # notes are not dimensions
    assert _looks_like_dimension("3 otw.") and not _looks_like_dimension("Politechnika Bydgoska 2026")


def test_unrecognized_text_makes_the_part_uncertain(tmp_path):
    part = next(p for p in extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf").parts if p.drawing_no == "01.01.02")
    odd = part.model_copy(update={"unrecognized_texts": ["SR12"]})
    report, _ = process_part(odd, ManualSpecInterpreter(REPO_ROOT / "tests" / "fixtures" / "reczne"), out_dir=tmp_path,
                             pdf_path=REPO_ROOT / "rysunki" / "cad_07_1.pdf")
    assert report.status == "NIEPEWNE"
    assert any(a.id == "PROG-NIEROZPOZNANE-TEKSTY" and "SR12" in a.description for a in report.ambiguities)


def test_prompt_unchanged_for_dimensions_without_tolerance():
    """Stored AI answers are keyed by the prompt: plain dimensions must not change it."""
    part = next(p for p in extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf").parts if p.drawing_no == "01.01.01")
    prompt = build_user_prompt(part)
    assert "tolerance" not in prompt and "reference" not in prompt
