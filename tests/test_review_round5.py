"""Regression tests for the fifth external review."""
from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from drawing2cad.confirm import CONFIRMED, DecisionStore, apply_decisions
from drawing2cad.interpreter import CostTracker, FatalInterpreterError, LLMInterpreter
from drawing2cad.pdf_extract import _looks_like_dimension, _parse_region_dimensions, _TextCluster, extract_pdf
from drawing2cad.schema import Ambiguity, VerificationReport

REPO_ROOT = Path(__file__).resolve().parent.parent


def _c(text, x=0.0, y=0.0):
    return _TextCluster(raw_text=text, decoded_text=text, bbox=(x, y, x + 10, y + 5), direction=(1.0, 0.0), size=10.0)


def _report(volume: float) -> VerificationReport:
    amb = Ambiguity(id="PROG-OTWOR-CZESCIOWY", description="Otwór przez część ścianek.", assumption="Do potwierdzenia.",
                    severity="ostrzezenie", source="program")
    return VerificationReport(drawing_no="X1", name="Część", status="NIEPEWNE", passed=True,
                              bounding_box_mm=(10.0, 10.0, 10.0), volume_mm3=volume, ambiguities=[amb])


def test_confirmation_does_not_carry_over_to_another_model(tmp_path):
    store = DecisionStore(tmp_path / "d.json")
    first = _report(900.0)
    store.set(first.drawing_no, first.ambiguities[0], CONFIRMED, model=f"{','.join(f'{v:.3f}' for v in first.bounding_box_mm)}|{first.volume_mm3:.2f}")
    assert apply_decisions(first, store).status == "OK"
    assert apply_decisions(_report(950.0), store).status == "NIEPEWNE"  # same remark, different solid


def test_ai_without_image_is_never_settled():
    from drawing2cad.blocks import build_part
    from drawing2cad.drawing_compare import extract_views
    from drawing2cad.schema import PartSpec
    from drawing2cad.verifier import verify_part

    pdf = REPO_ROOT / "rysunki" / "cad_07_1.pdf"
    part = next(p for p in extract_pdf(pdf).parts if p.drawing_no == "01.01.02")
    spec = PartSpec.model_validate_json((REPO_ROOT / "tests" / "fixtures" / "reczne" / "01.01.02.json").read_text(encoding="utf-8"))
    spec.ambiguities = [Ambiguity(id="AI-BEZ-OBRAZU", description="Bez obrazu.", assumption="?", severity="ostrzezenie")]
    report = verify_part(spec, build_part(spec), part.dimensions, views=extract_views(str(pdf), part.page_index, part.bbox))
    assert report.status == "NIEPEWNE"
    assert any(a.id == "AI-BEZ-OBRAZU" and a.source == "program" for a in report.ambiguities)


def test_budget_rejects_nan_and_infinity():
    for bad in (math.nan, math.inf, 0.0, -1.0):
        with pytest.raises(ValueError):
            CostTracker((0.3, 0.006, 1.2), bad)


def test_missing_usage_counts_the_worst_case(tmp_path):
    part = next(p for p in extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf").parts if p.drawing_no == "01.01.02")
    reply = (REPO_ROOT / "tests" / "fixtures" / "reczne" / "01.01.02.json").read_text(encoding="utf-8")
    calls = []

    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content=reply), finish_reason="stop")])

    interp = LLMInterpreter(provider="deepseek", model="m", api_key="k", vision=False, max_cost=0.1)
    interp._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    interp.interpret(part)
    assert interp.costs.total_usd >= 0.08  # worst case, not 0
    with pytest.raises(FatalInterpreterError, match="Limit kosztów"):
        interp.interpret(part, feedback_errors=["x"], attempt=2)
    assert len(calls) == 1


def test_lone_zero_is_a_tolerance_and_notes_are_not_dimensions():
    dims, unknown = _parse_region_dimensions([_c("Ø20", 100, 100), _c("+0,021", 115, 97), _c("0", 115, 104)])
    assert len(dims) == 1 and dims[0].tolerance == "+0,021/0"
    assert unknown == []
    for text in ("Skala 1:2", "1/2", "2026-03-15", "Ra 1,6", "S235JR"):
        assert not _looks_like_dimension(text), text
    dims, _ = _parse_region_dimensions([_c("g=3")])
    assert dims and dims[0].value == 3.0


def test_app_rejects_foreign_host_and_reports_outside_the_job_folder(tmp_path):
    from fastapi.testclient import TestClient

    from drawing2cad.app.jobs import AppConfig
    from drawing2cad.app.server import create_app

    client = TestClient(create_app(AppConfig(work_dir=tmp_path)))  # default hosts: no 'testserver'
    assert client.get("/api/info").status_code == 403
