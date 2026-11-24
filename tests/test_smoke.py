"""Stage 1 smoke tests: package imports, CLI runs, CAD kernel works."""
import cadquery as cq

import drawing2cad
from drawing2cad.cli import main


def test_version():
    assert drawing2cad.__version__ == "0.1.0"


def test_cli_help(capsys):
    assert main([]) == 0
    assert "drawing2cad" in capsys.readouterr().out


def test_cadquery_kernel():
    box = cq.Workplane("XY").box(10, 20, 30).val()
    assert box.isValid()
    assert abs(box.Volume() - 6000) < 1e-6
