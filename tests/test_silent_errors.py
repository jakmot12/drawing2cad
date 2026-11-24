"""Silent errors: wrong models that once passed as OK / NIEPEWNE.

Sources: the external code review (13 cases + 2 extras) and real wrong DeepSeek answers.
Every case must now end as BŁĄD, and must really be wrong according to porownaj.py
(so the test cannot pass by accident on a model that is actually fine).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from drawing2cad.blocks import BuildError, build_part
from drawing2cad.drawing_compare import extract_views, parse_scale
from drawing2cad.pdf_extract import extract_pdf
from drawing2cad.schema import PartSpec
from drawing2cad.verifier import verify_part

REPO_ROOT = Path(__file__).resolve().parent.parent
PDF = REPO_ROOT / "rysunki" / "cad_07_1.pdf"
RECZNE = REPO_ROOT / "tests" / "fixtures" / "reczne"
DEEPSEEK = REPO_ROOT / "tests" / "fixtures" / "deepseek"
TESTSET = REPO_ROOT / "podnosnik-testset"
sys.path.insert(0, str(TESTSET))
from porownaj import TOL_OBJ, porownaj, wczytaj  # noqa: E402

REF = {f.name[:8]: f for f in (TESTSET / "step").glob("*.step")}


def _spec(n: str) -> dict:
    return json.loads((RECZNE / f"{n}.json").read_text(encoding="utf-8"))


def _ops(d: dict) -> list[dict]:
    return d["operations"]


def _case_sruba_run5():
    return "01.01.05", json.loads((DEEPSEEK / "01.01.05_run5_czop_w_srodku.json").read_text(encoding="utf-8"))


def _case_korona_no_holes_diameter_as_radius():
    d = _spec("01.01.01")
    d["operations"] = [o for o in _ops(d) if o["op"] != "otwor"]
    for b in d["dimension_bindings"]:
        if b["target"] == "otwor_srednica":
            b["target"] = "promien"
    d["ambiguities"] = []
    return "01.01.01", d


def _case_podstawa_wall_8():
    d = _spec("01.01.02")
    _ops(d)[0]["wall_thickness"] = 8.0
    return "01.01.02", d


def _case_sruba_threads_metadata_only():
    d = _spec("01.01.05")
    _ops(d)[0]["length"] = 100.0
    _ops(d)[1].update(offset=100.0, length=210.0)
    return "01.01.05", d


def _case_oska_swapped_lengths():
    d = _spec("01.01.04")
    _ops(d)[0]["length"] = 15.0
    _ops(d)[1].update(offset=7.5, length=15.0)
    _ops(d)[2].update(offset=-28.5, length=21.0)
    return "01.01.04", d


def _case_podstawa_holes_z12():
    d = _spec("01.01.02")
    _ops(d)[1]["positions"] = [[-12.5, 12.0], [12.5, 12.0]]
    return "01.01.02", d


def _case_podstawa_holes_shifted():
    d = _spec("01.01.02")
    _ops(d)[1]["positions"] = [[-2.5, 16.0], [22.5, 16.0]]
    return "01.01.02", d


def _case_podstawa_one_fillet():
    d = _spec("01.01.02")
    prof = _ops(d)[0]["side_profile"]
    prof["vertex_fillets"] = prof["vertex_fillets"][:1]
    return "01.01.02", d


def _case_oska_cross_hole_off_centre():
    d = _spec("01.01.04")
    _ops(d)[4]["positions"] = [[0.0, 4.0]]
    return "01.01.04", d


def _case_podstawa_diameter_10_14():
    d = _spec("01.01.02")
    _ops(d)[1]["diameter"] = 10.14
    return "01.01.02", d


def _case_korona_one_hole():
    d = _spec("01.01.01")
    _ops(d)[2]["positions"] = [[0.0, 10.0]]
    for b in d["dimension_bindings"]:
        if b["target"] == "otwor_srednica":
            b["multiplicity"] = 1
    return "01.01.01", d


def _case_korona_v_shifted():
    d = _spec("01.01.01")
    _ops(d)[1]["center_x"] = 10.0
    return "01.01.01", d


def _case_oska_run5_one_chamfer():
    return "01.01.04", json.loads((DEEPSEEK / "01.01.04_run5_fazka_jednostronna.json").read_text(encoding="utf-8"))


def _case_korona_wall_8():
    d = _spec("01.01.01")
    _ops(d)[0]["wall_thickness"] = 8.0
    return "01.01.01", d


def _case_ramie_web_8():
    d = _spec("01.01.06")
    _ops(d)[0]["web_thickness"] = 8.0
    return "01.01.06", d


def _case_ramie_run2_shifted_profile():
    """Real run 2 answer pattern: profile in a 0..149 frame -> 211.5 mm long part."""
    d = _spec("01.01.06")
    _ops(d)[0]["web_length"] = None
    _ops(d)[0]["side_profile"] = {"kind": "slot", "cx": 74.5, "cy": 12.0, "length": 149.0, "width": 24.0}
    _ops(d)[1]["positions"] = [[12.0, 12.0], [137.0, 12.0]]
    return "01.01.06", d


def _case_oska_run2_cube_body():
    """Real run 2-4 answer pattern: body as a 21 mm cube, labelled 'plaska'."""
    d = _spec("01.01.04")
    d["part_type"] = "plaska"
    _ops(d)[0] = {"op": "plyta_z_profilu", "plane": "XY", "thickness": 21.0, "centered_extrude": True,
                  "shapes": [{"kind": "rect", "cx": 0.0, "cy": 0.0, "width": 21.0, "height": 21.0}]}
    return "01.01.04", d


def _case_korona_run4_holes_z8():
    """Real run 4 answer: undimensioned holes guessed at z = 8 (drawn at z = 10)."""
    d = _spec("01.01.01")
    _ops(d)[2]["positions"] = [[-15.0, 8.0], [15.0, 8.0]]
    return "01.01.01", d


CASES = {
    "G01 Śruba run5: czop kwadratowy w środku": _case_sruba_run5,
    "G02 Korona bez otworów, Ø jako promien": _case_korona_no_holes_diameter_as_radius,
    "G03 Podstawa ścianki 8 zamiast 3": _case_podstawa_wall_8,
    "G04 Śruba: długości gwintów tylko w metadanych": _case_sruba_threads_metadata_only,
    "G05 Ośka: zamienione długości korpusu i czopa": _case_oska_swapped_lengths,
    "G06 Podstawa otwory z=12 zamiast 16": _case_podstawa_holes_z12,
    "G07 Podstawa otwory przesunięte o +10": _case_podstawa_holes_shifted,
    "G08 Podstawa tylko 1 z 4 zaokrągleń R10": _case_podstawa_one_fillet,
    "G09 Ośka otwór poprzeczny poza środkiem": _case_oska_cross_hole_off_centre,
    "G10 Podstawa otwory Ø10.14": _case_podstawa_diameter_10_14,
    "G11 Korona 1 otwór, multiplicity=1": _case_korona_one_hole,
    "G12 Korona wycięcie V przesunięte": _case_korona_v_shifted,
    "G13 Ośka run5: fazka tylko na jednym czopie": _case_oska_run5_one_chamfer,
    "G+1 Korona ścianki 8 zamiast 3": _case_korona_wall_8,
    "G+2 Ramię środnik 8 zamiast 3": _case_ramie_web_8,
    "R2 Ramię run2: profil przesunięty (211,5 mm)": _case_ramie_run2_shifted_profile,
    "R2 Ośka run2: korpus jako kostka 21": _case_oska_run2_cube_body,
    "R4 Korona run4: otwory na z=8": _case_korona_run4_holes_z8,
}


# ---- second review round (and own probes) ----

def _case_podstawa_holes_one_wall():
    d = _spec("01.01.02")
    _ops(d)[1]["depth"] = 20.0
    return "01.01.02", d


def _case_oska_hidden_cavity_no_cross_hole():
    d = _spec("01.01.04")
    _ops(d)[4] = {"op": "otwor", "plane": "XY", "diameter": 8.5, "positions": [[0.0, 0.0]], "depth": 10.0}
    return "01.01.04", d


def _case_oska_cross_hole_half_depth():
    d = _spec("01.01.04")
    _ops(d)[4]["depth"] = 8.0
    return "01.01.04", d


def _case_oska_without_cross_hole():
    d = _spec("01.01.04")
    del _ops(d)[4]
    return "01.01.04", d


def _case_sruba_v_notch_hidden_by_break():
    d = _spec("01.01.05")
    _ops(d).append({"op": "wyciecie_v", "plane": "XY", "center_x": 160.0, "top_z": 5.0, "depth": 3.0,
                    "angle_deg": 90.0, "direction": "down"})
    return "01.01.05", d


def _case_sruba_hole_hidden_by_break():
    d = _spec("01.01.05")
    _ops(d).append({"op": "otwor", "plane": "XY", "diameter": 3.0, "positions": [[159.5, 0.0]]})
    return "01.01.05", d


def _case_podstawa_holes_shift_028():
    d = _spec("01.01.02")
    _ops(d)[1]["positions"] = [[-12.22, 16.0], [12.78, 16.0]]
    return "01.01.02", d


def _case_korona_v_shift_028():
    d = _spec("01.01.01")
    _ops(d)[1]["center_x"] = 0.28
    return "01.01.01", d


def _case_sruba_tang_972():
    d = _spec("01.01.05")
    _ops(d)[0]["length"] = 9.72
    _ops(d)[1].update(offset=9.72, length=300.28)
    return "01.01.05", d


def _case_dzwignia_square_hole_blind():
    d = _spec("01.01.03")
    _ops(d)[3]["depth"] = 3.0
    return "01.01.03", d


def _case_dzwignia_edge_fillet_r035():
    d = _spec("01.01.03")
    _ops(d)[2]["radius"] = 0.35
    d["ambiguities"] = []
    return "01.01.03", d


def _case_dzwignia_square_corners_r05():
    d = _spec("01.01.03")
    _ops(d)[3]["corner_radius"] = 0.5
    d["ambiguities"] = []
    return "01.01.03", d


CASES.update({
    "N01 Podstawa otwory tylko w 1 ściance": _case_podstawa_holes_one_wall,
    "N02 Ośka wewnętrzna komora zamiast otworu": _case_oska_hidden_cavity_no_cross_hole,
    "N03 Ośka otwór poprzeczny do połowy": _case_oska_cross_hole_half_depth,
    "N04 Ośka bez otworu poprzecznego (wyjątek gwintu)": _case_oska_without_cross_hole,
    "N05 Śruba wycięcie V w przerwanej części": _case_sruba_v_notch_hidden_by_break,
    "N06 Śruba otwór w przerwanej części": _case_sruba_hole_hidden_by_break,
    "N07 Podstawa otwory przesunięte o 0,28": _case_podstawa_holes_shift_028,
    "N08 Korona wycięcie V przesunięte o 0,28": _case_korona_v_shift_028,
    "N09 Śruba czop 9,72 zamiast 10": _case_sruba_tang_972,
    "N10 Dźwignia otwór □7 nieprzelotowy": _case_dzwignia_square_hole_blind,
    "N11 Dźwignia R0,35 zamiast R1 na krawędziach": _case_dzwignia_edge_fillet_r035,
    "N12 Dźwignia naroża □7 R0,5 zamiast R1": _case_dzwignia_square_corners_r05,
})


# ---- third review round ----

def _case_oska_tube_along_view():
    d = _spec("01.01.04")
    _ops(d).insert(4, {"op": "walec", "axis": "Y", "diameter": 10.0, "length": 60.0, "offset": 0.0, "centered": True})
    _ops(d)[5]["diameter"] = 8.5
    return "01.01.04", d


def _case_podstawa_lower_fillets_r91():
    d = _spec("01.01.02")
    f = _ops(d)[0]["side_profile"]["vertex_fillets"]
    f[0]["radius"] = 9.1
    f[3]["radius"] = 9.1
    return "01.01.02", d


def _case_podstawa_holes_shift_007():
    d = _spec("01.01.02")
    _ops(d)[1]["positions"] = [[-12.43, 16.0], [12.57, 16.0]]
    return "01.01.02", d


def _case_dzwignia_edge_fillet_r082():
    d = _spec("01.01.03")
    _ops(d)[2]["radius"] = 0.82
    d["ambiguities"] = []
    return "01.01.03", d


CASES.update({
    "T01 Ośka rura Ø10x60 w kierunku patrzenia": _case_oska_tube_along_view,
    "T02 Podstawa dolne zaokrąglenia R9,1": _case_podstawa_lower_fillets_r91,
    "T03 Podstawa otwory przesunięte o 0,07": _case_podstawa_holes_shift_007,
    "T04 Dźwignia R0,82 zamiast R1": _case_dzwignia_edge_fillet_r082,
})

def _case_sruba_tang_993():
    d = _spec("01.01.05")
    _ops(d)[0]["length"] = 9.93
    _ops(d)[1].update(offset=9.93, length=300.07)
    return "01.01.05", d


CASES["T05 Śruba czop 9,93 (poniżej dokładności rysunku)"] = _case_sruba_tang_993

# ---- fourth review round ----

def _case_oska_hollow_pin_via_depth():
    d = _spec("01.01.04")
    _ops(d).append({"op": "otwor", "plane": "XY", "diameter": 7.6, "positions": [[0.0, 0.0]], "depth": 15.0})
    for b in d["dimension_bindings"]:
        if b["dim_id"] == "D2":
            b["target"] = "glebokosc_otworu"
    return "01.01.04", d


def _case_oska_tube_16_along_view():
    d = _spec("01.01.04")
    _ops(d).insert(4, {"op": "walec", "axis": "Y", "diameter": 10.0, "length": 16.0, "offset": 0.0, "centered": True})
    _ops(d)[5]["diameter"] = 8.5
    return "01.01.04", d


def _case_ramie_length_14909():
    d = _spec("01.01.06")
    _ops(d)[0]["length"] = 149.09
    _ops(d)[0]["side_profile"]["length"] = 149.09
    return "01.01.06", d


def _case_dzwignia_r096():
    d = _spec("01.01.03")
    _ops(d)[2]["radius"] = 0.96
    d["ambiguities"] = []
    return "01.01.03", d


def _case_sruba_d10019():
    d = _spec("01.01.05")
    _ops(d)[1]["diameter"] = 10.019
    return "01.01.05", d


CASES.update({
    "C01 Ośka wydrążony czop (wymiar przepięty na głębokość)": _case_oska_hollow_pin_via_depth,
    "C02 Ośka rura Ø10x16 w kierunku patrzenia": _case_oska_tube_16_along_view,
    "C03 Ramię długość 149,09": _case_ramie_length_14909,
    "C04 Dźwignia R0,96 zamiast R1": _case_dzwignia_r096,
    "C05 Śruba Ø10,019": _case_sruba_d10019,
})

# cases where the drawing itself cannot decide: NIEPEWNE with this named reason is the right answer
EXPECT_UNCERTAIN = {
    "N01 Podstawa otwory tylko w 1 ściance": "PROG-OTWOR-CZESCIOWY",
    "T05 Śruba czop 9,93 (poniżej dokładności rysunku)": "PROG-POLOZENIE-STOPNIA",
    "C03 Ramię długość 149,09": "PROG-GABARYT-X",
}


@pytest.fixture(scope="module")
def parts():
    out = {}
    for p in extract_pdf(PDF).parts:
        out[p.drawing_no] = (p, extract_views(str(PDF), p.page_index, p.bbox),
                             parse_scale(p.title_block.skala) if p.title_block else None)
    return out


@pytest.mark.parametrize("name", list(CASES))
def test_silent_error_is_now_reported_as_error(parts, name):
    drawing_no, data = CASES[name]()
    spec = PartSpec.model_validate(data)
    try:
        solid = build_part(spec)
    except BuildError as exc:  # the pipeline reports a build error as BŁĄD and retries
        assert "side_profile" in str(exc) or "Krok" in str(exc)
        return

    # the case must really be wrong, otherwise the test proves nothing
    nadmiar, brak = porownaj(wczytaj(str(REF[drawing_no])), solid.val())
    assert nadmiar >= TOL_OBJ or brak >= TOL_OBJ, f"{name}: model is actually correct"

    part, views, scale = parts[drawing_no]
    report = verify_part(spec, solid, part.dimensions, views=views, sheet_scale=scale)
    if name in EXPECT_UNCERTAIN:
        assert report.status in ("NIEPEWNE", "BŁĄD"), f"{name}: {report.status}"
        if report.status == "NIEPEWNE":
            assert any(a.id == EXPECT_UNCERTAIN[name] for a in report.ambiguities), report.ambiguities
        return
    assert report.status == "BŁĄD", f"{name}: {report.status}, errors={report.errors}"
    assert report.errors
