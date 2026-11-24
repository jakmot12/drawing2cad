"""Stage 2 tests: every CAD block individually + all 6 jack parts vs reference STEPs via porownaj.py."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

from drawing2cad.blocks import BuildError, build_part
from drawing2cad.schema import (
    CeownikOp,
    CzopKwadratowyOp,
    FazkaOp,
    OtworKwadratowyOp,
    OtworOp,
    PartSpec,
    PlytaZProfiluOp,
    ProfileCircle,
    ProfileRect,
    WalecOp,
    WyciecieVOp,
    ZaokraglenieKrawedziOp,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTSET_DIR = REPO_ROOT / "podnosnik-testset"
RECZNE = REPO_ROOT / "tests" / "fixtures" / "reczne"

sys.path.insert(0, str(TESTSET_DIR))
from porownaj import TOL_OBJ, porownaj, wczytaj  # noqa: E402


# ---------------------------------------------------------------------------
# Unit tests for individual CAD blocks
# ---------------------------------------------------------------------------


def test_block_plyta_z_profilu_and_otwor_kwadratowy():
    spec = PartSpec(
        drawing_no="99.00.01",
        name="TestPlate",
        part_type="plaska",
        operations=[
            PlytaZProfiluOp(
                thickness=5.0,
                shapes=[ProfileRect(width=40.0, height=20.0)],
            ),
            OtworKwadratowyOp(size=10.0, corner_radius=0.0, positions=[(0.0, 0.0)]),
        ],
    )
    solid = build_part(spec).val()
    assert solid.isValid()
    assert abs(solid.Volume() - (40 * 20 - 10 * 10) * 5) < 1e-3


def test_block_ceownik_and_wyciecie_v():
    spec = PartSpec(
        drawing_no="99.00.02",
        name="TestChannel",
        part_type="blacha_gieta",
        operations=[
            CeownikOp(
                length=40.0,
                outer_width=20.0,
                height=15.0,
                wall_thickness=2.0,
                web_thickness=2.0,
                open_side="bottom",
            ),
            WyciecieVOp(center_x=0.0, top_z=15.0, depth=5.0, angle_deg=90.0),
        ],
    )
    solid = build_part(spec).val()
    assert solid.isValid()
    # Channel cross-section area = 20*15 - 16*13 = 92; minus V-notch through top + walls
    assert solid.Volume() < 40.0 * 92.0


def test_block_walec_czop_fazka_otwor():
    spec = PartSpec(
        drawing_no="99.00.03",
        name="TestShaft",
        part_type="toczona",
        operations=[
            CzopKwadratowyOp(axis="X", size=8.0, length=10.0, corner_radius=1.0, offset=0.0),
            WalecOp(axis="X", diameter=12.0, length=30.0, offset=10.0),
            FazkaOp(size=1.0, angle_deg=45.0, face_selectors=[">X"]),
            OtworOp(plane="XY", diameter=4.0, positions=[(25.0, 0.0)]),
        ],
    )
    solid = build_part(spec).val()
    assert solid.isValid()
    bb = solid.BoundingBox()
    assert abs(bb.xlen - 40.0) < 1e-3
    assert abs(bb.ylen - 12.0) < 1e-3
    assert abs(bb.zlen - 12.0) < 1e-3


def test_block_zaokraglenie_near_points_and_face_edges():
    spec = PartSpec(
        drawing_no="99.00.04",
        name="TestFillet",
        part_type="plaska",
        operations=[
            PlytaZProfiluOp(
                thickness=10.0,
                shapes=[
                    ProfileCircle(cx=0.0, cy=0.0, radius=10.0),
                    ProfileRect(cx=20.0, cy=0.0, width=40.0, height=12.0),
                ],
            ),
            ZaokraglenieKrawedziOp(
                radius=2.0,
                selector="near_points",
                axis="Z",
                points=[(40.0, 6.0), (40.0, -6.0)],
            ),
            ZaokraglenieKrawedziOp(
                radius=1.0,
                selector="face_edges",
                face_selector=">Z or <Z",
            ),
        ],
    )
    solid = build_part(spec).val()
    assert solid.isValid()
    expected_upper = (math.pi * 100 + 40 * 12) * 10
    assert 0 < solid.Volume() < expected_upper


def test_build_error_when_modifying_without_base_solid():
    spec = PartSpec(
        drawing_no="99.00.99",
        name="Invalid",
        part_type="plaska",
        operations=[OtworOp(plane="XY", diameter=5.0, positions=[(0.0, 0.0)])],
    )
    with pytest.raises(BuildError):
        build_part(spec)


# ---------------------------------------------------------------------------
# Stage 2 Golden Test: all 6 scissor-jack parts built from JSON match STEP refs
# ---------------------------------------------------------------------------

STEP_FILES = {
    "01.01.01": TESTSET_DIR / "step" / "01.01.01_korona.step",
    "01.01.02": TESTSET_DIR / "step" / "01.01.02_podstawa.step",
    "01.01.03": TESTSET_DIR / "step" / "01.01.03_dzwignia.step",
    "01.01.04": TESTSET_DIR / "step" / "01.01.04_oska.step",
    "01.01.05": TESTSET_DIR / "step" / "01.01.05_sruba.step",
    "01.01.06": TESTSET_DIR / "step" / "01.01.06_ramie.step",
}


@pytest.mark.parametrize("drawing_no", sorted(STEP_FILES.keys()))
def test_all_six_jack_parts_match_reference_steps(drawing_no: str):
    spec = PartSpec.model_validate_json(
        (RECZNE / f"{drawing_no}.json").read_text(encoding="utf-8")
    )
    built = build_part(spec).val()
    ref = wczytaj(str(STEP_FILES[drawing_no]))

    nadmiar, brak = porownaj(ref, built)
    assert nadmiar < TOL_OBJ, f"{drawing_no}: nadmiar materiału {nadmiar:.2f} mm³"
    assert brak < TOL_OBJ, f"{drawing_no}: brak materiału {brak:.2f} mm³"
