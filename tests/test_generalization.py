"""Generalisation: the verifier on parts that are NOT the scissor-lift parts it was tuned on.

Every case is a realistic part built from the block library, drawn on a synthetic sheet
(first-angle views, thin dimension and centre lines, frame and title block) and checked with
the dimensions such a drawing would carry:
  - the correct model must pass (no false BŁĄD; NIEPEWNE only with the named reason listed),
  - every wrong variant must end as BŁĄD, or NIEPEWNE with a reason found by the program.
Each wrong variant is first proven different from the correct model by volume (porownaj.py),
so a test cannot pass on a variant that is actually fine.
"""
from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from drawing2cad.blocks import BuildError, build_part
from drawing2cad.drawing_compare import extract_views
from drawing2cad.schema import ExtractedDimension, PartSpec
from drawing2cad.verifier import verify_part

from sheet_helpers import FRONT, draw_sheet, layout_views

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "podnosnik-testset"))
from porownaj import TOL_OBJ, porownaj  # noqa: E402

WHOLE_PAGE = (0, 0, 595, 842)
TOP = ("+Z", 0)


@dataclass
class Case:
    spec: dict
    dims: list[tuple]  # (text, dim_type, value, target, multiplicity, secondary)
    main: tuple[str, int]
    others: list[str]
    scale: float = 1.0
    title_scale: bool = True  # the title block states the scale (else the program estimates it)
    hidden: bool = False  # hidden edges drawn as dashed lines
    expect: str | None = None  # None = OK; else the id of the program's NIEPEWNE reason
    variants: dict = field(default_factory=dict)  # name -> function(spec dict) -> None (in place)


def _d(text, dtype, value, target, mult=1, secondary=None):
    return (text, dtype, value, target, mult, secondary)


def _extracted(case: Case) -> list[ExtractedDimension]:
    return [
        ExtractedDimension(id=f"D{i}", raw_text=t, decoded_text=t, dim_type=dt, value=v, multiplicity=m,
                           secondary_value=s, bbox=(0, 0, 1, 1))
        for i, (t, dt, v, _tg, m, s) in enumerate(case.dims, 1)
    ]


def _spec(case: Case, data: dict | None = None) -> PartSpec:
    d = copy.deepcopy(data if data is not None else case.spec)
    d["dimension_bindings"] = [
        {"dim_id": f"D{i}", "target": tg, "expected_value": v, "multiplicity": m,
         **({"secondary_value": s} if s is not None else {})}
        for i, (_t, _dt, v, tg, m, s) in enumerate(case.dims, 1)
    ]
    d.setdefault("threads", [])
    d.setdefault("ambiguities", [])
    return PartSpec.model_validate(d)


def _ops(d):
    return d["operations"]


# ---------------------------------------------------------------------------
# P01 plate with a grid of holes and an edge recess
# ---------------------------------------------------------------------------

def _p01():
    outline = [[-60, -40], [60, -40], [60, 40], [15, 40], [15, 20], [-15, 20], [-15, 40], [-60, 40]]
    grid = [[x, y] for y in (-25, 5) for x in (-40, 0, 40)]
    spec = {"drawing_no": "G.01", "name": "Płyta z otworami", "part_type": "plaska", "operations": [
        {"op": "plyta_z_profilu", "plane": "XY", "thickness": 8, "shapes": [{"kind": "polygon", "points": outline}]},
        {"op": "otwor", "plane": "XY", "diameter": 8, "positions": grid},
    ]}
    dims = [_d("120", "dlugosc", 120, "gabaryt"), _d("80", "dlugosc", 80, "gabaryt"),
            _d("8", "dlugosc", 8, "grubosc"), _d("6xØ8", "srednica", 8, "otwor_srednica", 6),
            _d("40", "dlugosc", 40, "otwor_rozstaw"), _d("30", "dlugosc", 30, "otwor_rozstaw"),
            _d("15", "dlugosc", 15, "otwor_wysokosc"), _d("30", "dlugosc", 30, "dlugosc_odcinka"),
            _d("20", "dlugosc", 20, "dlugosc_odcinka")]

    def hole_shifted(d):
        _ops(d)[1]["positions"][2] = [41, -25]

    def one_hole_d7(d):
        _ops(d)[1]["positions"].pop(4)
        _ops(d).append({"op": "otwor", "plane": "XY", "diameter": 7, "positions": [[0, 5]]})

    def hole_missing(d):
        _ops(d)[1]["positions"].pop(0)

    def recess_deeper(d):
        pts = _ops(d)[0]["shapes"][0]["points"]
        pts[4], pts[5] = [15, 18], [-15, 18]

    def recess_off_centre(d):
        pts = _ops(d)[0]["shapes"][0]["points"]
        for p in pts[3:7]:
            p[0] += 0.3

    def thinner(d):
        _ops(d)[0]["thickness"] = 6

    return Case(spec, dims, TOP, ["below"], variants={
        "otwór przesunięty o 1 mm": hole_shifted, "jeden otwór Ø7": one_hole_d7, "brak otworu": hole_missing,
        "wybranie głębsze o 2 mm": recess_deeper, "wybranie przesunięte o 0,3 mm": recess_off_centre,
        "grubość 6 zamiast 8": thinner})


# ---------------------------------------------------------------------------
# P02 bent sheet angle (L) with holes in both legs
# ---------------------------------------------------------------------------

def _p02():
    prof = {"kind": "polygon", "points": [[0, 0], [40, 0], [40, 4], [4, 4], [4, 30], [0, 30]],
            "vertex_fillets": [{"point": [4, 4], "radius": 4}, {"point": [0, 0], "radius": 8}]}
    spec = {"drawing_no": "G.02", "name": "Kątownik", "part_type": "blacha_gieta", "operations": [
        {"op": "plyta_z_profilu", "plane": "XZ", "thickness": 60, "centered_extrude": True, "shapes": [prof]},
        {"op": "otwor", "plane": "XY", "diameter": 6, "positions": [[25, -15], [25, 15]]},
        {"op": "otwor", "plane": "YZ", "diameter": 6, "positions": [[-15, 18], [15, 18]]},
    ]}
    dims = [_d("40", "dlugosc", 40, "gabaryt"), _d("30", "dlugosc", 30, "gabaryt"), _d("60", "dlugosc", 60, "gabaryt"),
            _d("4", "dlugosc", 4, "grubosc"), _d("R4", "promien", 4, "promien"),
            _d("4xØ6", "srednica", 6, "otwor_srednica", 4), _d("25", "dlugosc", 25, "otwor_wysokosc"),
            _d("30", "dlugosc", 30, "otwor_rozstaw"), _d("18", "dlugosc", 18, "otwor_wysokosc")]

    def inner_r3(d):
        _ops(d)[0]["shapes"][0]["vertex_fillets"][0]["radius"] = 3

    def sharp_outer(d):
        _ops(d)[0]["shapes"][0]["vertex_fillets"].pop(1)

    def holes_z20(d):
        _ops(d)[2]["positions"] = [[-15, 20], [15, 20]]

    def leg_42(d):
        _ops(d)[0]["shapes"][0]["points"][1:3] = [[42, 0], [42, 4]]

    def one_leg_hole_only_spacing_32(d):
        _ops(d)[1]["positions"] = [[25, -16], [25, 16]]

    return Case(spec, dims, FRONT, ["below", "right"], variants={
        "promień wewnętrzny R3": inner_r3, "ostre zewnętrzne naroże": sharp_outer, "otwory na z=20": holes_z20,
        "półka 42 zamiast 40": leg_42, "rozstaw otworów półki 32": one_leg_hole_only_spacing_32})


# ---------------------------------------------------------------------------
# P03 stepped shaft with chamfers and a groove (one view)
# ---------------------------------------------------------------------------

def _p03():
    spec = {"drawing_no": "G.03", "name": "Wałek", "part_type": "toczona", "operations": [
        {"op": "walec", "axis": "X", "diameter": 20, "length": 27, "offset": 0},
        {"op": "walec", "axis": "X", "diameter": 17, "length": 3, "offset": 27},
        {"op": "walec", "axis": "X", "diameter": 30, "length": 40, "offset": 30},
        {"op": "walec", "axis": "X", "diameter": 25, "length": 50, "offset": 70},
        {"op": "fazka", "size": 1, "face_selectors": [">X", "<X"]},
    ]}
    dims = [_d("Ø20", "srednica", 20, "walec_srednica"), _d("Ø17", "srednica", 17, "walec_srednica"),
            _d("Ø30", "srednica", 30, "walec_srednica"), _d("Ø25", "srednica", 25, "walec_srednica"),
            _d("120", "dlugosc", 120, "gabaryt"), _d("30", "dlugosc", 30, "dlugosc_odcinka"),
            _d("3", "dlugosc", 3, "dlugosc_odcinka"), _d("50", "dlugosc", 50, "dlugosc_odcinka"),
            _d("2x1x45°", "fazka", 1, "fazka", 2, 45)]

    def one_chamfer(d):
        _ops(d)[4]["face_selectors"] = [">X"]

    def groove_moved(d):
        _ops(d)[0]["length"] = 25
        _ops(d)[1]["offset"] = 25
        _ops(d).insert(2, {"op": "walec", "axis": "X", "diameter": 20, "length": 2, "offset": 28})

    def d32(d):
        _ops(d)[2]["diameter"] = 32

    def step_72(d):
        _ops(d)[2]["length"] = 42
        _ops(d)[3].update(offset=72, length=48)

    def groove_d18(d):
        _ops(d)[1]["diameter"] = 18

    return Case(spec, dims, FRONT, [], scale=0.5, title_scale=False, variants={
        "fazka tylko na jednym końcu": one_chamfer, "rowek przesunięty o 2 mm": groove_moved, "Ø32 zamiast Ø30": d32,
        "stopień na 72 zamiast 70": step_72, "rowek Ø18 zamiast Ø17": groove_d18})


# ---------------------------------------------------------------------------
# P04 bushing, chamfered inside and outside
# ---------------------------------------------------------------------------

def _p04():
    spec = {"drawing_no": "G.04", "name": "Tuleja", "part_type": "toczona", "operations": [
        {"op": "walec", "axis": "Z", "diameter": 30, "length": 40, "offset": 0},
        {"op": "otwor", "plane": "XY", "diameter": 20, "positions": [[0, 0]]},
        {"op": "fazka", "size": 1, "face_selectors": [">Z", "<Z"]},
    ]}
    dims = [_d("Ø30", "srednica", 30, "walec_srednica"), _d("Ø20", "srednica", 20, "otwor_srednica"),
            _d("40", "dlugosc", 40, "gabaryt"), _d("2x1x45°", "fazka", 1, "fazka", 2, 45),
            _d("2x1x45°", "fazka", 1, "fazka", 2, 45)]

    def no_inner_chamfer(d):
        _ops(d)[1], _ops(d)[2] = _ops(d)[2], _ops(d)[1]

    def bore_18(d):
        _ops(d)[1]["diameter"] = 18

    def bore_eccentric(d):
        _ops(d)[1]["positions"] = [[0.5, 0]]

    def bore_blind(d):
        _ops(d)[1]["depth"] = 35

    return Case(spec, dims, FRONT, ["below"], hidden=True, variants={
        "brak fazek wewnętrznych": no_inner_chamfer, "otwór Ø18": bore_18, "otwór mimośrodowy 0,5 mm": bore_eccentric,
        "otwór nieprzelotowy": bore_blind})


# ---------------------------------------------------------------------------
# P05 flange (D > L) with a bolt circle
# ---------------------------------------------------------------------------

def _p05():
    spec = {"drawing_no": "G.05", "name": "Kołnierz", "part_type": "toczona", "operations": [
        {"op": "walec", "axis": "Z", "diameter": 80, "length": 12, "offset": 0},
        {"op": "walec", "axis": "Z", "diameter": 40, "length": 18, "offset": 12},
        {"op": "otwor", "plane": "XY", "diameter": 25, "positions": [[0, 0]]},
        {"op": "otwor", "plane": "XY", "diameter": 9, "positions": [[30, 0], [0, 30], [-30, 0], [0, -30]]},
    ]}
    dims = [_d("Ø80", "srednica", 80, "walec_srednica"), _d("Ø40", "srednica", 40, "walec_srednica"),
            _d("Ø25", "srednica", 25, "otwor_srednica"), _d("4xØ9", "srednica", 9, "otwor_srednica", 4),
            _d("Ø60", "srednica", 60, "otwor_rozstaw"), _d("12", "dlugosc", 12, "dlugosc_odcinka"),
            _d("30", "dlugosc", 30, "gabaryt")]

    def pcd_62(d):
        _ops(d)[3]["positions"] = [[31, 0], [0, 31], [-31, 0], [0, -31]]

    def three_holes(d):
        _ops(d)[3]["positions"].pop()

    def hub_42(d):
        _ops(d)[1]["diameter"] = 42

    return Case(spec, dims, FRONT, ["below"], variants={
        "podziałka Ø62": pcd_62, "3 otwory zamiast 4": three_holes, "piasta Ø42": hub_42})


# ---------------------------------------------------------------------------
# P06 flat bar with a slotted hole
# ---------------------------------------------------------------------------

def _p06():
    spec = {"drawing_no": "G.06", "name": "Płaskownik z fasolką", "part_type": "plaska", "operations": [
        {"op": "plyta_z_profilu", "plane": "XY", "thickness": 6,
         "shapes": [{"kind": "rect", "cx": 0, "cy": 0, "width": 100, "height": 30}]},
        {"op": "otwor", "plane": "XY", "diameter": 10, "positions": [[-5, 0], [5, 0]]},
        {"op": "otwor_kwadratowy", "plane": "XY", "size": 10, "positions": [[0, 0]]},
        {"op": "otwor", "plane": "XY", "diameter": 8, "positions": [[-40, 0], [40, 0]]},
    ]}
    dims = [_d("100", "dlugosc", 100, "gabaryt"), _d("30", "dlugosc", 30, "gabaryt"), _d("6", "dlugosc", 6, "grubosc"),
            _d("2xØ8", "srednica", 8, "otwor_srednica", 2), _d("80", "dlugosc", 80, "otwor_rozstaw"),
            _d("R5", "promien", 5, "promien"), _d("10", "dlugosc", 10, "dlugosc_odcinka")]

    def slot_shifted(d):
        _ops(d)[1]["positions"] = [[-3, 0], [7, 0]]
        _ops(d)[2]["positions"] = [[2, 0]]

    def slot_r6(d):
        _ops(d)[1].update(diameter=12, positions=[[-6, 0], [6, 0]])
        _ops(d)[2]["size"] = 12

    def slot_longer(d):
        _ops(d)[1]["positions"] = [[-6, 0], [6, 0]]
        _ops(d)[2]["positions"] = [[-1, 0], [1, 0]]

    def holes_78(d):
        _ops(d)[3]["positions"] = [[-39, 0], [39, 0]]

    return Case(spec, dims, TOP, ["below"], variants={
        "fasolka przesunięta o 2 mm": slot_shifted, "fasolka R6": slot_r6, "fasolka dłuższa o 2 mm": slot_longer,
        "rozstaw otworów 78": holes_78})


# ---------------------------------------------------------------------------
# P07 plate with a square hole and rounded corners
# ---------------------------------------------------------------------------

def _p07():
    corners = [[-30, -30], [30, -30], [30, 30], [-30, 30]]
    spec = {"drawing_no": "G.07", "name": "Płyta z otworem kwadratowym", "part_type": "plaska", "operations": [
        {"op": "plyta_z_profilu", "plane": "XY", "thickness": 5,
         "shapes": [{"kind": "polygon", "points": corners,
                     "vertex_fillets": [{"point": p, "radius": 5} for p in corners]}]},
        {"op": "otwor_kwadratowy", "plane": "XY", "size": 20, "corner_radius": 2, "positions": [[0, 0]]},
        {"op": "otwor", "plane": "XY", "diameter": 6, "positions": [[x, y] for x in (-24, 24) for y in (-24, 24)]},
    ]}
    dims = [_d("60", "dlugosc", 60, "gabaryt"), _d("60", "dlugosc", 60, "gabaryt"), _d("5", "dlugosc", 5, "grubosc"),
            _d("□20", "kwadrat", 20, "kwadrat"), _d("R2", "promien", 2, "promien"),
            _d("4xR5", "promien", 5, "promien", 4), _d("4xØ6", "srednica", 6, "otwor_srednica", 4),
            _d("48", "dlugosc", 48, "otwor_rozstaw"), _d("48", "dlugosc", 48, "otwor_rozstaw")]

    def square_22(d):
        _ops(d)[1]["size"] = 22

    def square_sharp(d):
        _ops(d)[1]["corner_radius"] = 0

    def corners_r6(d):
        for vf in _ops(d)[0]["shapes"][0]["vertex_fillets"]:
            vf["radius"] = 6

    def holes_46(d):
        _ops(d)[2]["positions"] = [[x, y] for x in (-23, 23) for y in (-23, 23)]

    def square_off_centre(d):
        _ops(d)[1]["positions"] = [[0.5, 0]]

    return Case(spec, dims, TOP, ["below"], variants={
        "otwór □22": square_22, "otwór kwadratowy bez R2": square_sharp, "naroża R6": corners_r6,
        "rozstaw otworów 46": holes_46, "otwór kwadratowy przesunięty o 0,5": square_off_centre})


# ---------------------------------------------------------------------------
# P08 block with a sloped face and a hole, three views
# ---------------------------------------------------------------------------

def _p08():
    spec = {"drawing_no": "G.08", "name": "Klocek ze skosem", "part_type": "plaska", "operations": [
        {"op": "plyta_z_profilu", "plane": "XZ", "thickness": 30, "centered_extrude": True,
         "shapes": [{"kind": "polygon", "points": [[0, 0], [60, 0], [60, 20], [40, 40], [0, 40]]}]},
        {"op": "otwor", "plane": "XZ", "diameter": 10, "positions": [[20, 20]]},
    ]}
    dims = [_d("60", "dlugosc", 60, "gabaryt"), _d("40", "dlugosc", 40, "gabaryt"), _d("30", "dlugosc", 30, "gabaryt"),
            _d("20", "dlugosc", 20, "dlugosc_odcinka"), _d("45°", "kat", 45, "kat_sciecia"),
            _d("Ø10", "srednica", 10, "otwor_srednica"), _d("20", "dlugosc", 20, "otwor_wysokosc"),
            _d("20", "dlugosc", 20, "otwor_wysokosc")]

    def slope_higher(d):
        _ops(d)[0]["shapes"][0]["points"][2:4] = [[60, 25], [45, 40]]

    def slope_angle(d):
        _ops(d)[0]["shapes"][0]["points"][3] = [45, 40]

    def hole_x22(d):
        _ops(d)[1]["positions"] = [[22, 20]]

    def narrower(d):
        _ops(d)[0]["thickness"] = 28

    return Case(spec, dims, FRONT, ["below", "right"], variants={
        "skos od wysokości 25": slope_higher, "skos pod innym kątem": slope_angle, "otwór na x=22": hole_x22,
        "szerokość 28 zamiast 30": narrower})


# ---------------------------------------------------------------------------
# P09 gasket drawn in one view, thickness in a note
# ---------------------------------------------------------------------------

def _p09():
    spec = {"drawing_no": "G.09", "name": "Podkładka", "part_type": "plaska", "operations": [
        {"op": "plyta_z_profilu", "plane": "XY", "thickness": 3,
         "shapes": [{"kind": "rect", "cx": 0, "cy": 0, "width": 80, "height": 50}]},
        {"op": "otwor", "plane": "XY", "diameter": 10, "positions": [[-30, 0], [30, 0]]},
    ]}
    dims = [_d("80", "dlugosc", 80, "gabaryt"), _d("50", "dlugosc", 50, "gabaryt"), _d("g=3", "dlugosc", 3, "grubosc"),
            _d("2xØ10", "srednica", 10, "otwor_srednica", 2), _d("60", "dlugosc", 60, "otwor_rozstaw")]

    def thicker(d):
        _ops(d)[0]["thickness"] = 4

    def holes_62(d):
        _ops(d)[1]["positions"] = [[-31, 0], [31, 0]]

    def hidden_pocket(d):
        _ops(d).append({"op": "otwor_kwadratowy", "plane": "XY", "size": 10, "positions": [[0, 0]], "depth": 1.5})

    return Case(spec, dims, TOP, [], variants={
        "grubość 4": thicker, "rozstaw 62": holes_62, "kieszeń nieprzelotowa": hidden_pocket})


# ---------------------------------------------------------------------------
# P10 bracket in three first-angle views (front, top below, left view on the right)
# ---------------------------------------------------------------------------

def _p10(notch_y: int = 1):
    s = notch_y
    base = [[0, -20], [80, -20], [80, 10 * s], [65, 10 * s], [65, 20 * s], [0, 20 * s]]
    if s < 0:
        base = [[0, 20], [80, 20], [80, -10], [65, -10], [65, -20], [0, -20]]
    spec = {"drawing_no": "G.10", "name": "Wspornik", "part_type": "plaska", "operations": [
        {"op": "plyta_z_profilu", "plane": "XY", "thickness": 10, "shapes": [{"kind": "polygon", "points": base}]},
        {"op": "plyta_z_profilu", "plane": "YZ", "thickness": 10,
         "shapes": [{"kind": "rect", "cx": 0, "cy": 35, "width": 40, "height": 50}]},
        {"op": "otwor", "plane": "YZ", "diameter": 12, "positions": [[0, 40]]},
        {"op": "otwor", "plane": "XY", "diameter": 8, "positions": [[45, 0]]},
    ]}
    dims = [_d("80", "dlugosc", 80, "gabaryt"), _d("40", "dlugosc", 40, "gabaryt"), _d("60", "dlugosc", 60, "gabaryt"),
            _d("10", "dlugosc", 10, "grubosc"), _d("10", "dlugosc", 10, "grubosc"),
            _d("Ø12", "srednica", 12, "otwor_srednica"), _d("40", "dlugosc", 40, "otwor_wysokosc"),
            _d("Ø8", "srednica", 8, "otwor_srednica"), _d("45", "dlugosc", 45, "otwor_wysokosc"),
            _d("15", "dlugosc", 15, "dlugosc_odcinka"), _d("10", "dlugosc", 10, "dlugosc_odcinka")]

    def mirrored(d):
        _ops(d)[0] = _p10(-1).spec["operations"][0]

    def hole_z42(d):
        _ops(d)[2]["positions"] = [[0, 42]]

    def upright_12(d):
        _ops(d)[1]["thickness"] = 12

    def base_hole_blind(d):
        _ops(d)[3]["depth"] = 5

    def no_notch(d):
        _ops(d)[0]["shapes"][0]["points"] = [[0, -20], [80, -20], [80, 20], [0, 20]]

    return Case(spec, dims, FRONT, ["below", "right"], hidden=True, variants={
        "odbicie lustrzane (wycięcie po drugiej stronie)": mirrored, "otwór Ø12 na z=42": hole_z42,
        "ścianka 12 zamiast 10": upright_12, "otwór Ø8 nieprzelotowy": base_hole_blind, "brak wycięcia": no_notch})


# ---------------------------------------------------------------------------
# P11 V-block
# ---------------------------------------------------------------------------

def _p11():
    spec = {"drawing_no": "G.11", "name": "Pryzma", "part_type": "plaska", "operations": [
        {"op": "plyta_z_profilu", "plane": "XY", "thickness": 30,
         "shapes": [{"kind": "rect", "cx": 0, "cy": 0, "width": 60, "height": 40}]},
        {"op": "wyciecie_v", "plane": "XZ", "center_x": 0, "top_z": 30, "depth": 10, "angle_deg": 90},
    ]}
    dims = [_d("60", "dlugosc", 60, "gabaryt"), _d("40", "dlugosc", 40, "gabaryt"), _d("30", "dlugosc", 30, "gabaryt"),
            _d("90°", "kat", 90, "kat_wyciecia"), _d("10", "dlugosc", 10, "glebokosc_wyciecia")]

    def angle_80(d):
        _ops(d)[1]["angle_deg"] = 80

    def deeper(d):
        _ops(d)[1]["depth"] = 12

    def shifted(d):
        _ops(d)[1]["center_x"] = 2

    return Case(spec, dims, FRONT, ["below"], variants={
        "kąt 80°": angle_80, "głębokość 12": deeper, "wycięcie przesunięte o 2 mm": shifted})


# ---------------------------------------------------------------------------
# P12 pin with a head and a cross hole, drawn 2:1
# ---------------------------------------------------------------------------

def _p12():
    spec = {"drawing_no": "G.12", "name": "Sworzeń", "part_type": "toczona", "operations": [
        {"op": "walec", "axis": "Z", "diameter": 12, "length": 4, "offset": 0},
        {"op": "walec", "axis": "Z", "diameter": 8, "length": 30, "offset": 4},
        {"op": "otwor", "plane": "XZ", "diameter": 3, "positions": [[0, 30]]},
        {"op": "fazka", "size": 0.5, "face_selectors": [">Z"]},
    ]}
    dims = [_d("Ø12", "srednica", 12, "walec_srednica"), _d("Ø8", "srednica", 8, "walec_srednica"),
            _d("4", "dlugosc", 4, "dlugosc_odcinka"), _d("34", "dlugosc", 34, "gabaryt"),
            _d("Ø3", "srednica", 3, "otwor_srednica"), _d("26", "dlugosc", 26, "otwor_wysokosc"),
            _d("0,5x45°", "fazka", 0.5, "fazka", 1, 45)]

    def hole_25(d):
        _ops(d)[2]["positions"] = [[0, 29]]

    def hole_d35(d):
        _ops(d)[2]["diameter"] = 3.5

    def head_5(d):
        _ops(d)[0]["length"] = 5
        _ops(d)[1].update(offset=5, length=29)

    def no_chamfer(d):
        _ops(d).pop()

    return Case(spec, dims, FRONT, ["right"], scale=2.0, variants={
        "otwór 25 od łba": hole_25, "otwór Ø3,5": hole_d35, "łeb 5 zamiast 4": head_5, "brak fazki": no_chamfer})


# ---------------------------------------------------------------------------
# P13 plate with counterbored holes
# ---------------------------------------------------------------------------

def _p13():
    spec = {"drawing_no": "G.13", "name": "Płyta z pogłębieniami", "part_type": "plaska", "operations": [
        {"op": "plyta_z_profilu", "plane": "XZ", "thickness": 12,
         "shapes": [{"kind": "rect", "cx": 0, "cy": 0, "width": 80, "height": 40}]},
        {"op": "otwor", "plane": "XZ", "diameter": 6.6, "positions": [[-25, 0], [25, 0]]},
        {"op": "otwor", "plane": "XZ", "diameter": 11, "positions": [[-25, 0], [25, 0]], "depth": 6.4},
    ]}
    dims = [_d("80", "dlugosc", 80, "gabaryt"), _d("40", "dlugosc", 40, "gabaryt"), _d("12", "dlugosc", 12, "grubosc"),
            _d("2xØ6,6", "srednica", 6.6, "otwor_srednica", 2), _d("2xØ11", "srednica", 11, "otwor_srednica", 2),
            _d("6,4", "dlugosc", 6.4, "glebokosc_otworu"), _d("50", "dlugosc", 50, "otwor_rozstaw")]

    def deeper(d):
        _ops(d)[2]["depth"] = 8

    def one_counterbore(d):
        _ops(d)[2]["positions"] = [[-25, 0]]

    def counterbore_12(d):
        _ops(d)[2]["diameter"] = 12

    def counterbore_through(d):
        _ops(d)[2]["depth"] = None
        _ops(d)[1]["diameter"] = 6.6

    return Case(spec, dims, ("+Y", 0), ["below"], hidden=True, variants={
        "pogłębienie 8 zamiast 6,4": deeper, "pogłębienie tylko przy jednym otworze": one_counterbore,
        "pogłębienie Ø12": counterbore_12, "pogłębienie na wylot": counterbore_through})


# ---------------------------------------------------------------------------
# P14 chamfered plate, hidden lines drawn
# ---------------------------------------------------------------------------

def _p14():
    spec = {"drawing_no": "G.14", "name": "Płytka z fazkami", "part_type": "plaska", "operations": [
        {"op": "plyta_z_profilu", "plane": "XY", "thickness": 10,
         "shapes": [{"kind": "rect", "cx": 0, "cy": 0, "width": 50, "height": 30}]},
        {"op": "fazka", "size": 1, "face_selectors": [">Z"]},
        {"op": "otwor", "plane": "XY", "diameter": 8, "positions": [[0, 0]]},
    ]}
    dims = [_d("50", "dlugosc", 50, "gabaryt"), _d("30", "dlugosc", 30, "gabaryt"), _d("10", "dlugosc", 10, "grubosc"),
            _d("1x45°", "fazka", 1, "fazka", 1, 45), _d("Ø8", "srednica", 8, "otwor_srednica")]

    def chamfer_2(d):
        _ops(d)[1]["size"] = 2

    def chamfer_both_faces(d):
        _ops(d)[1]["face_selectors"] = [">Z", "<Z"]

    def no_chamfer(d):
        _ops(d).pop(1)

    def hole_chamfered_too(d):
        _ops(d)[1], _ops(d)[2] = _ops(d)[2], _ops(d)[1]

    return Case(spec, dims, TOP, ["below"], hidden=True, variants={
        "fazka 2 zamiast 1": chamfer_2, "fazki na obu stronach": chamfer_both_faces, "brak fazki": no_chamfer,
        "sfazowany także otwór": hole_chamfered_too})


# ---------------------------------------------------------------------------
# P15 shaft with a rounded step (toroidal fillets seen as arcs in the side view)
# ---------------------------------------------------------------------------

def _p15(radius: float = 2, axis: str = "X"):
    spec = {"drawing_no": "G.15", "name": "Wałek z zaokrąglonym stopniem", "part_type": "toczona", "operations": [
        {"op": "walec", "axis": axis, "diameter": 20, "length": 30, "offset": 0},
        {"op": "walec", "axis": axis, "diameter": 30, "length": 40, "offset": 30},
        # the step face at 30 along the axis: its inner (root) and outer edges are rounded
        {"op": "zaokraglenie_krawedzi", "radius": radius, "selector": "face_edges", "face_selector": f"<{axis}[1]"},
    ]}
    dims = [_d("Ø20", "srednica", 20, "walec_srednica"), _d("Ø30", "srednica", 30, "walec_srednica"),
            _d("70", "dlugosc", 70, "gabaryt"), _d("40", "dlugosc", 40, "dlugosc_odcinka"),
            _d("2xR2", "promien", 2, "promien", 2)]

    def r1(d):
        _ops(d)[2]["radius"] = 1

    def no_rounding(d):
        _ops(d).pop(2)

    def step_at_32(d):
        _ops(d)[0]["length"] = 32
        _ops(d)[1].update(offset=32, length=38)

    return Case(spec, dims, FRONT, [], variants={
        "R1 zamiast R2": r1, "brak zaokrągleń": no_rounding, "stopień na 32 zamiast 30": step_at_32})


PARTS = {
    "P01 płyta z siatką otworów i wybraniem": _p01,
    "P02 kątownik gięty L": _p02,
    "P03 wałek stopniowy z rowkiem (1 widok)": _p03,
    "P04 tuleja z fazkami": _p04,
    "P05 kołnierz z otworami na podziałce": _p05,
    "P06 płaskownik z fasolką": _p06,
    "P07 płyta z otworem kwadratowym": _p07,
    "P08 klocek ze skosem (3 widoki)": _p08,
    "P09 podkładka w 1 widoku": _p09,
    "P10 wspornik w 3 widokach": _p10,
    "P11 pryzma": _p11,
    "P12 sworzeń 2:1": _p12,
    "P13 płyta z pogłębieniami": _p13,
    "P14 płytka z fazkami": _p14,
    "P15 wałek z zaokrąglonym stopniem": _p15,
}


@pytest.fixture(scope="module")
def sheets(tmp_path_factory):
    """Each part drawn once: (case, drawn views, scale for verify_part)."""
    out = {}
    tmp = tmp_path_factory.mktemp("sheets")
    for name, make in PARTS.items():
        case = make()
        solid = build_part(_spec(case))
        pdf = tmp / f"{case.spec['drawing_no']}.pdf"
        layout = layout_views(solid, case.main, case.others, case.scale)
        draw_sheet(solid, pdf, layout, scale=case.scale, frame="rect", annotate=True, title_block=True,
                   hidden=case.hidden)
        views = extract_views(str(pdf), 0, WHOLE_PAGE)
        assert len(views) == 1 + len(case.others), f"{name}: {len(views)} views"
        out[name] = (case, solid, views, case.scale if case.title_scale else None)
    return out


def _verify(sheets, name, data=None):
    case, good, views, scale = sheets[name]
    spec = _spec(case, data)
    solid = build_part(spec)
    return solid, verify_part(spec, solid, _extracted(case), views=views, sheet_scale=scale)


def _principal_moments(shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, props)
    m = props.MatrixOfInertia()
    return np.linalg.eigvalsh(np.array([[m.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)]))


def _really_different(good, solid) -> bool:
    """Different volume or principal moments of inertia prove it at once (no rigid motion
    changes them); otherwise (e.g. a mirror image) the aligned comparison decides
    (porownaj.py: principal axes, 24 orientations, no mirroring)."""
    if abs(good.val().Volume() - solid.val().Volume()) >= TOL_OBJ:
        return True
    mg, ms = _principal_moments(good.val()), _principal_moments(solid.val())
    if np.abs(mg - ms).max() > 1e-4 * mg.max():
        return True
    nadmiar, brak = porownaj(good.val(), solid.val())
    return nadmiar >= TOL_OBJ or brak >= TOL_OBJ


def _blocking_program_reasons(report):
    return [a for a in report.ambiguities if a.source == "program" and a.severity in ("blad_rysunku", "ostrzezenie")]


@pytest.mark.parametrize("name", list(PARTS))
def test_correct_model_passes(sheets, name):
    case = sheets[name][0]
    _, report = _verify(sheets, name)
    assert report.passed, f"{name}: false BŁĄD: {report.errors}"
    if case.expect is None:
        assert report.status == "OK", f"{name}: {report.status}: {[a.description for a in report.ambiguities]}"
    else:
        assert report.status == "NIEPEWNE"
        assert {a.id for a in _blocking_program_reasons(report)} == {case.expect}, report.ambiguities


VARIANTS = [(p, v) for p, make in PARTS.items() for v in make().variants]


@pytest.mark.parametrize(("name", "variant"), VARIANTS, ids=[f"{p[:3]}-{v}" for p, v in VARIANTS])
def test_wrong_variant_is_caught(sheets, name, variant):
    case, good, _views, _scale = sheets[name]
    data = copy.deepcopy(case.spec)
    case.variants[variant](data)
    try:
        solid, report = _verify(sheets, name, data)
    except BuildError:
        return  # the pipeline reports a build error as BŁĄD
    assert _really_different(good, solid), f"{name} / {variant}: the variant is actually correct"
    if report.status == "BŁĄD":
        assert report.errors
        return
    assert report.status == "NIEPEWNE", f"{name} / {variant}: silent OK"
    assert _blocking_program_reasons(report), f"{name} / {variant}: NIEPEWNE without a program reason"


@pytest.mark.xfail(strict=True, reason=(
    "Known limitation: views are matched only in the 24 axis-aligned orientations of the model. "
    "A turned part whose hole pattern is turned about the part's own axis by an angle other than "
    "a multiple of 90 deg is the same part, but its projections do not match the drawing: BŁĄD."))
def test_flange_turned_about_its_axis_is_the_same_part(sheets):
    name = "P05 kołnierz z otworami na podziałce"
    case, good, _views, _scale = sheets[name]
    data = copy.deepcopy(case.spec)
    a = 30 * 0.5 ** 0.5
    data["operations"][3]["positions"] = [[a, a], [-a, a], [-a, -a], [a, -a]]
    _, report = _verify(sheets, name, data)  # 4 equal holes on Ø60 turned by 45 deg: the same part
    assert report.passed, report.errors


# ---------------------------------------------------------------------------
# Focused regressions for the false alarms found above (no drawn sheet needed)
# ---------------------------------------------------------------------------

def _report(data, dims):
    spec = PartSpec.model_validate(data)
    ext = [ExtractedDimension(id=f"D{i}", raw_text=t, decoded_text=t, dim_type=dt, value=v, multiplicity=m,
                              bbox=(0, 0, 1, 1)) for i, (t, dt, v, m) in enumerate(dims, 1)]
    return verify_part(spec, build_part(spec), ext)


def _status(report, dim_id):
    return next(c for c in report.checks if c.dim_id == dim_id).status


@pytest.mark.parametrize(("radius", "ok"), [(25.0, True), (26.0, False)])
def test_pitch_circle_of_three_holes(radius, ok):
    """Ø50 pitch circle of 3 holes at 120 deg: no two holes are 50 mm apart."""
    pos = [[radius * np.cos(np.radians(a)), radius * np.sin(np.radians(a))] for a in (90, 210, 330)]
    data = {"drawing_no": "R1", "name": "Tarcza", "part_type": "toczona", "operations": [
        {"op": "walec", "axis": "Z", "diameter": 80, "length": 10},
        {"op": "otwor", "plane": "XY", "diameter": 6, "positions": pos}],
        "dimension_bindings": [{"dim_id": "D1", "target": "otwor_rozstaw"}, {"dim_id": "D2", "target": "otwor_srednica"}]}
    report = _report(data, [("Ø50", "srednica", 50.0, 1), ("3xØ6", "srednica", 6.0, 3)])
    assert (_status(report, "D1") != "BŁĄD") == ok, report.errors


@pytest.mark.parametrize(("z", "ok"), [(30.0, True), (29.0, False)])
def test_hole_dimensioned_from_a_shoulder(z, ok):
    """Cross hole of a pin dimensioned from the underside of the head, not from the part's end."""
    data = {"drawing_no": "R2", "name": "Sworzeń", "part_type": "toczona", "operations": [
        {"op": "walec", "axis": "Z", "diameter": 12, "length": 4},
        {"op": "walec", "axis": "Z", "diameter": 8, "length": 30, "offset": 4},
        {"op": "otwor", "plane": "XZ", "diameter": 3, "positions": [[0, z]]}],
        "dimension_bindings": [{"dim_id": "D1", "target": "otwor_wysokosc"}]}
    report = _report(data, [("26", "dlugosc", 26.0, 1)])
    assert (_status(report, "D1") != "BŁĄD") == ok, report.errors


def test_wall_thickness_with_a_hole_in_the_middle_of_the_face():
    """The parameter centre of the upright's face lies in its hole: the wall is probed elsewhere."""
    data = copy.deepcopy(_p10().spec)
    data["dimension_bindings"] = [{"dim_id": "D1", "target": "grubosc"}, {"dim_id": "D2", "target": "grubosc"}]
    report = _report(data, [("10", "dlugosc", 10.0, 1), ("10", "dlugosc", 10.0, 1)])
    assert not any("ścianek grubości" in e for e in report.errors), report.errors


@pytest.mark.parametrize(("depth", "ok"), [(6.4, True), (8.0, False)])
def test_counterbore_depth_is_measured(depth, ok):
    data = copy.deepcopy(_p13().spec)
    data["operations"][2]["depth"] = depth
    data["dimension_bindings"] = [{"dim_id": "D1", "target": "glebokosc_otworu"},
                                  {"dim_id": "D2", "target": "otwor_srednica"},
                                  {"dim_id": "D3", "target": "otwor_srednica"}]
    report = _report(data, [("6,4", "dlugosc", 6.4, 1), ("2xØ6,6", "srednica", 6.6, 2), ("2xØ11", "srednica", 11.0, 2)])
    assert (_status(report, "D1") != "BŁĄD") == ok, report.errors


def test_counterbore_without_depth_dimension_is_an_error():
    data = copy.deepcopy(_p13().spec)
    data["dimension_bindings"] = [{"dim_id": "D1", "target": "otwor_srednica"}, {"dim_id": "D2", "target": "otwor_srednica"}]
    report = _report(data, [("2xØ6,6", "srednica", 6.6, 2), ("2xØ11", "srednica", 11.0, 2)])
    assert any("Ø11" in e and "kończy się w materiale" in e for e in report.errors), report.errors


def test_slot_from_touching_holes_is_one_solid():
    """Two holes of one operation that touch (the ends of a slot) must not leave loose material."""
    assert len(build_part(PartSpec.model_validate(_p06().spec)).val().Solids()) == 1


# ---------------------------------------------------------------------------
# Rounded shaft step: the rounding counts only where a view shows its profile arc
# ---------------------------------------------------------------------------

_HIDDEN_ROUND = "zaokrąglenia, których żaden widok nie pokazuje z profilu"


def _drawn(tmp_path, drawn_spec, views, model_spec=None, with_r=True, axis="X", model=None):
    """Draw `drawn_spec` (None: the rounded shaft), verify `model_spec` (None: the rounded shaft)
    or the solid `model` built some other way."""
    case = _p15(axis=axis)
    if not with_r:
        case.dims = [d for d in case.dims if d[1] != "promien"]
    drawn = build_part(_spec(case, drawn_spec))
    pdf = tmp_path / "sheet.pdf"
    draw_sheet(drawn, pdf, layout_views(drawn, views[0], views[1]), frame="rect", annotate=True, title_block=True)
    if model is None:
        model = build_part(_spec(case, model_spec))
    return verify_part(_spec(case, model_spec), model, _extracted(case),
                       views=extract_views(str(pdf), 0, WHOLE_PAGE), sheet_scale=1.0)


def test_rounded_step_seen_only_end_on_is_not_ok(tmp_path):
    """Only the end view (along the shaft axis): no view shows the roundings edge-on."""
    report = _drawn(tmp_path, None, (("-X", 0), []))
    assert report.status != "OK"
    assert any(_HIDDEN_ROUND in (a.assumption or "") for a in report.ambiguities), report.ambiguities


def test_rounded_step_drawn_sharp_is_not_ok(tmp_path):
    """The drawing shows a sharp step (no arcs, no R dimension); the model is rounded."""
    sharp = copy.deepcopy(_p15().spec)
    sharp["operations"].pop(2)
    report = _drawn(tmp_path, sharp, (FRONT, []), with_r=False)
    assert report.status != "OK", report.ambiguities
    assert report.errors or _blocking_program_reasons(report)


# ---------------------------------------------------------------------------
# Roundings the drawing does not show: hidden profiles, views along the axis, trimmed tori
# ---------------------------------------------------------------------------

def _compare(tmp_path, drawn, model, main, others, dims=()):
    """Draw the CadQuery solid `drawn`, compare the solid `model` with that drawing."""
    from drawing2cad.drawing_compare import compare_views

    pdf = tmp_path / "sheet.pdf"
    draw_sheet(drawn, pdf, layout_views(drawn, main, others), frame="rect", title_block=True)
    views = extract_views(str(pdf), 0, WHOLE_PAGE)
    assert len(views) == 1 + len(others)
    return compare_views(model.val().wrapped, views, list(dims), 1.0)


def _hidden_round(results) -> bool:
    return any(_HIDDEN_ROUND in n for r in results for n in r.notes)


def _rib_and_boss(boss_round: float = 0.0):
    """Plate 40x60x10; at the front (y -15..-5) a rib 16 wide with R2 roundings at its base,
    at the back (y = 15) a boss Ø16 lower than the rib (hidden behind it in the front view)."""
    import cadquery as cq

    plate = cq.Workplane("XY").box(40, 60, 10, centered=(True, True, False))
    rib = cq.Workplane("XY").box(16, 10, 10, centered=(True, True, False)).translate((0, -10, 10))
    part = plate.union(rib)
    part = part.edges("|Y").edges(cq.selectors.BoxSelector((-8.5, -16, 9.5), (8.5, -4, 10.5))).fillet(2)
    part = part.union(cq.Workplane("XY").circle(8).extrude(8).translate((0, 15, 10)))
    if boss_round:
        part = part.edges(cq.selectors.BoxSelector((-8.5, 6, 9.5), (8.5, 24, 10.5))).fillet(boss_round)
    return part


def test_rounding_hidden_behind_another_part_is_not_ok(tmp_path):
    """The boss's base is sharp on the drawing; in the model it is rounded R2. In the front view
    its profile arcs fall exactly on the drawn R2 arcs of the rib in front of it, but they are
    hidden: no view shows the boss rounding."""
    good, bad = _rib_and_boss(), _rib_and_boss(2.0)
    assert all(r.ok for r in _compare(tmp_path, good, good, FRONT, ["below"]))
    results = _compare(tmp_path, good, bad, FRONT, ["below"])
    assert not all(r.ok for r in results)
    assert _hidden_round(results)


def _plate_with_hole(round_r: float = 0.0):
    import cadquery as cq

    part = cq.Workplane("XY").box(60, 60, 15, centered=(True, True, False)).faces(">Z").workplane().hole(20)
    if round_r:
        part = part.edges(cq.selectors.BoxSelector((-10.5, -10.5, 14.5), (10.5, 10.5, 15.5))).fillet(round_r)
    return part


def test_tiny_rounding_of_a_hole_edge_is_not_ok(tmp_path):
    """Hole Ø20 sharp on the drawing, R0.15 in the model: in the view along the hole the
    rounding lies within the line tolerance of the drawn circle, but that view does not show
    its profile."""
    good, bad = _plate_with_hole(), _plate_with_hole(0.15)
    assert all(r.ok for r in _compare(tmp_path, good, good, FRONT, ["below"]))
    results = _compare(tmp_path, good, bad, FRONT, ["below"])
    assert not all(r.ok for r in results)
    assert _hidden_round(results)


def _m20_shaft(round_r: float = 0.0):
    import cadquery as cq

    part = cq.Workplane("YZ").circle(10).extrude(40)
    if round_r:
        part = part.faces("<X").edges().fillet(round_r)
    return part


def test_rounding_inside_a_thread_ring_is_not_explained_by_it(tmp_path):
    """M20 shaft drawn only end-on: the thread convention ring must not explain a R1 rounding
    of the shaft's end."""
    dims = [ExtractedDimension(id="D1", raw_text="M20", decoded_text="M20", dim_type="gwint", value=20.0,
                               bbox=(0, 0, 1, 1))]
    results = _compare(tmp_path, _m20_shaft(), _m20_shaft(1.0), ("-X", 0), [], dims)
    assert _hidden_round(results)


def test_torus_profile_needs_several_points_on_its_meridian():
    """A meridian that touches the face in one or two points (a trimmed torus) is no profile:
    such points lie on the face border, i.e. on some sharp line of the drawing anyway."""
    import cadquery as cq
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import TopAbs_IN, TopAbs_OUT

    from drawing2cad.drawing_compare import _torus_profiles

    face = cq.Solid.makeTorus(10, 2).Faces()[0].wrapped
    surf = BRepAdaptor_Surface(face, True)

    class OnlyNear:  # the face reaches the meridians only near one parameter v
        def __init__(self, keep):
            self.keep, self.state = keep, TopAbs_OUT

        def Perform(self, face, p, tol):
            self.state = TopAbs_IN if self.keep(p.Y()) else TopAbs_OUT

        def State(self):
            return self.state

    v0, v1 = surf.FirstVParameter(), surf.LastVParameter()
    step = (v1 - v0) / 9
    one = _torus_profiles(face, surf, OnlyNear(lambda v: abs(v - (v0 + step)) < 1e-9), 9)
    assert one == {}, one
    full = _torus_profiles(face, surf, OnlyNear(lambda v: True), 9)
    assert set(full) == {"+X", "-X", "+Y", "-Y"}


# the rounded step along every axis: the main view must look across the shaft
_STEP_VIEWS = {"X": (FRONT, []), "Y": (TOP, []), "Z": (FRONT, [])}


def _step_roundings(axis: str, root: float, outer: float):
    """The P15 shaft with the root and the outer edge of the step rounded separately (0 = sharp)."""
    import cadquery as cq

    data = copy.deepcopy(_p15(axis=axis).spec)
    data["operations"].pop(2)
    solid = build_part(_spec(_p15(axis=axis), data))
    for nth, r in ((0, root), (1, outer)):  # RadiusNth: 0 = the root circle Ø20, 1 = the outer one Ø30
        if r:
            solid = solid.faces(f"<{axis}[1]").edges(cq.selectors.RadiusNthSelector(nth)).fillet(r)
    return solid


@pytest.mark.parametrize("axis", ["X", "Y", "Z"])
def test_rounded_step_along_any_axis_is_ok(tmp_path, axis):
    report = _drawn(tmp_path, copy.deepcopy(_p15(axis=axis).spec), _STEP_VIEWS[axis], axis=axis,
                    model_spec=copy.deepcopy(_p15(axis=axis).spec))
    assert report.status == "OK", (report.errors, report.ambiguities)


@pytest.mark.parametrize("axis", ["X", "Y", "Z"])
@pytest.mark.parametrize(("root", "outer"), [(1, 1), (2, 1), (1, 2), (0, 2), (2, 0)],
                         ids=["oba R1", "zewnetrzne R1", "wewnetrzne R1", "wewnetrzne ostre", "zewnetrzne ostre"])
def test_rounded_step_with_a_wrong_rounding_is_not_ok(tmp_path, axis, root, outer):
    """Drawn: both step edges R2. Model: one or both of them different."""
    model = _step_roundings(axis, root, outer)
    good = _step_roundings(axis, 2, 2)
    assert _really_different(good, model)
    report = _drawn(tmp_path, copy.deepcopy(_p15(axis=axis).spec), _STEP_VIEWS[axis], axis=axis,
                    model_spec=copy.deepcopy(_p15(axis=axis).spec), model=model)
    assert report.status != "OK", (axis, root, outer)
    assert report.errors or _blocking_program_reasons(report)


@pytest.mark.parametrize("axis", ["X", "Y", "Z"])
def test_rounded_step_seen_only_end_on_is_not_ok_along_any_axis(tmp_path, axis):
    end_on = {"X": ("-X", 0), "Y": ("-Y", 0), "Z": ("+Z", 0)}[axis]
    report = _drawn(tmp_path, None, (end_on, []), axis=axis)
    assert report.status != "OK"
    assert any(_HIDDEN_ROUND in (a.assumption or "") for a in report.ambiguities), report.ambiguities
