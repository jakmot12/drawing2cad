"""Stage 3 tests: deterministic vector PDF extraction verified against ground_truth.json."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from drawing2cad.pdf_extract import decode_inventor_text, extract_pdf

REPO_ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH = json.loads(
    (REPO_ROOT / "podnosnik-testset" / "ground_truth.json").read_text(encoding="utf-8")
)


def test_decode_inventor_symbol_codes():
    assert decode_inventor_text("P", "AIGDT") == "Ø"
    assert decode_inventor_text("Q", "AIGDT") == "□"
    assert decode_inventor_text("90", "Tahoma") == "90°"
    assert decode_inventor_text("16", "Tahoma") == "Ø16"


def test_polish_letters_decoded_from_embedded_font_not_a_hand_table():
    """Letters missing from ToUnicode are read through the embedded font's own cmap."""
    names = {p.drawing_no: p.name for p in extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf").parts}
    assert names["01.01.03"] == "Dźwignia"
    assert names["01.01.04"] == "Ośka"
    assert names["01.01.05"] == "Śruba"
    assert names["01.01.06"] == "Ramię"
    asm = extract_pdf(REPO_ROOT / "rysunki" / "cad_08_1.pdf")
    assert asm.assembly_title_block.nazwa == "Podnośnik"
    assert "Jędrzeja Śniadeckich" in asm.assembly_title_block.instytucja
    assert "Inżynierii" in asm.assembly_title_block.instytucja


def test_assembly_dimensions_without_balloon_numbers():
    asm = extract_pdf(REPO_ROOT / "rysunki" / "cad_08_1.pdf")
    assert [(d.decoded_text, d.value) for d in asm.assembly_dimensions] == [("180", 180.0)]


def test_extract_cad_07_parts_and_dimensions(tmp_path):
    doc = extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf", render_dir=tmp_path / "img")
    assert not doc.is_assembly
    assert len(doc.parts) == 6

    by_no = {p.drawing_no: p for p in doc.parts}
    expected_names = {c["nr_rysunku"]: c["nazwa"] for c in GROUND_TRUTH["czesci"]}
    assert set(by_no.keys()) == set(expected_names.keys())
    for nr, name in expected_names.items():
        assert by_no[nr].name == name
        assert by_no[nr].image_path is not None
        assert Path(by_no[nr].image_path).is_file()

    # Check title block on single-part sheet (01.01.06 Ramię)
    tb = by_no["01.01.06"].title_block
    assert tb is not None
    assert tb.nazwa == "Ramię"
    assert tb.nr_rysunku == "01.01.06"
    assert tb.skala == "1:1"
    assert tb.autor == "Jan Kowalski"

    # Verify every expected numeric dimension from ground_truth.json is extracted
    for gt_part in GROUND_TRUTH["czesci"]:
        nr = gt_part["nr_rysunku"]
        ext_dims = by_no[nr].dimensions
        ext_vals = Counter(d.value for d in ext_dims if d.value is not None)
        for gt_dim in gt_part["wymiary"]:
            val = gt_dim["wartosc"]
            if isinstance(val, (int, float)):
                assert ext_vals[float(val)] >= 1, f"{nr}: brak wymiaru {val} ({gt_dim['opis']})"
            elif isinstance(val, str) and val.startswith("M"):
                assert any(
                    d.dim_type == "gwint" and d.thread_spec == val for d in ext_dims
                ), f"{nr}: brak gwintu {val}"
            elif isinstance(val, str) and "x" in val:
                size_str = val.split("x")[0]
                assert any(
                    d.dim_type == "fazka" and d.value == float(size_str) for d in ext_dims
                ), f"{nr}: brak fazki {val}"

    # Verify specific Inventor symbol detections
    assert any(d.decoded_text == "2xØ10" and d.multiplicity == 2 for d in by_no["01.01.01"].dimensions)
    assert any(d.decoded_text == "90°" and d.dim_type == "kat" for d in by_no["01.01.01"].dimensions)
    assert any(d.decoded_text == "7,5" and d.value == 7.5 for d in by_no["01.01.01"].dimensions)
    assert any(d.decoded_text == "□7" and d.dim_type == "kwadrat" for d in by_no["01.01.03"].dimensions)
    assert any(d.decoded_text == "Ø16" and d.dim_type == "srednica" for d in by_no["01.01.04"].dimensions)
    assert any(d.thread_spec == "M10 LH" for d in by_no["01.01.05"].dimensions)


def test_extract_cad_08_assembly_and_bom():
    doc = extract_pdf(REPO_ROOT / "rysunki" / "cad_08_1.pdf")
    assert doc.is_assembly
    assert doc.assembly_title_block is not None
    assert doc.assembly_title_block.nazwa == "Podnośnik"
    assert doc.assembly_title_block.nr_rysunku == "01.01.00"
    assert doc.assembly_title_block.skala == "1:2"

    assert len(doc.bom) == len(GROUND_TRUTH["bom"])
    for got, exp in zip(doc.bom, GROUND_TRUTH["bom"]):
        assert got.poz == exp["poz"]
        assert got.ilosc == exp["ilosc"]
        assert got.nazwa == exp["nazwa"]
        assert got.nr_rysunku == exp["nr_rysunku"]
        assert got.znormalizowana == bool(exp.get("znormalizowana", False))
