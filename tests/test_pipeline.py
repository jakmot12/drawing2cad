"""Stages 4, 6, 7 tests: AI cache, repair loop (max 3 attempts), HTML report, and CLI."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from drawing2cad.cli import main
from drawing2cad.interpreter import (
    FatalInterpreterError,
    LLMInterpreter,
    ManualSpecInterpreter,
    ResponseCache,
    build_user_prompt,
)
from drawing2cad.pdf_extract import extract_pdf
from drawing2cad.pipeline import process_part, process_pdf
from drawing2cad.schema import PartRegion, PartSpec

REPO_ROOT = Path(__file__).resolve().parent.parent
RECZNE = REPO_ROOT / "tests" / "fixtures" / "reczne"


class _RepairingMockInterpreter:
    """Attempt 1 produces wrong hole Ø12; attempt 2 fixes to Ø10 after reading feedback."""

    def __init__(self, good_spec: PartSpec) -> None:
        self.good_spec = good_spec
        self.received_feedback: list[list[str] | None] = []

    def interpret(
        self,
        part: PartRegion,
        feedback_errors: list[str] | None = None,
        attempt: int = 1,
    ) -> PartSpec:
        self.received_feedback.append(feedback_errors)
        if attempt == 1:
            bad = deepcopy(self.good_spec)
            bad.operations[1].diameter = 12.0  # type: ignore[union-attr]
            return bad
        return deepcopy(self.good_spec)


class _AlwaysBrokenInterpreter:
    def __init__(self, good_spec: PartSpec) -> None:
        self.good_spec = good_spec
        self.calls = 0

    def interpret(
        self,
        part: PartRegion,
        feedback_errors: list[str] | None = None,
        attempt: int = 1,
    ) -> PartSpec:
        self.calls += 1
        bad = deepcopy(self.good_spec)
        bad.operations[1].diameter = 14.0  # type: ignore[union-attr]
        return bad


def test_repair_loop_fixes_error_on_second_attempt(tmp_path):
    doc = extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf")
    podstawa = next(p for p in doc.parts if p.drawing_no == "01.01.02")
    good_spec = PartSpec.model_validate_json(
        (RECZNE / "01.01.02.json").read_text(encoding="utf-8")
    )

    mock = _RepairingMockInterpreter(good_spec)
    report, _ = process_part(podstawa, mock, out_dir=tmp_path, max_attempts=3,
                             pdf_path=REPO_ROOT / "rysunki" / "cad_07_1.pdf")

    assert report.passed
    assert report.status == "OK"
    assert report.attempts == 2
    assert mock.received_feedback[0] is None
    assert mock.received_feedback[1] is not None
    assert any("Ø10" in err for err in mock.received_feedback[1])
    assert report.step_path is not None
    assert Path(report.step_path).is_file()


def test_repair_loop_stops_after_max_attempts_and_marks_error(tmp_path):
    doc = extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf")
    podstawa = next(p for p in doc.parts if p.drawing_no == "01.01.02")
    good_spec = PartSpec.model_validate_json(
        (RECZNE / "01.01.02.json").read_text(encoding="utf-8")
    )

    mock = _AlwaysBrokenInterpreter(good_spec)
    report, _ = process_part(podstawa, mock, out_dir=tmp_path, max_attempts=3)

    assert not report.passed
    assert report.status == "BŁĄD"
    assert report.attempts == 3
    assert mock.calls == 3
    assert report.step_path is not None
    assert Path(report.step_path).name.startswith("BLEDNY_")


def test_response_cache_roundtrip(tmp_path):
    doc = extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf")
    korona = next(p for p in doc.parts if p.drawing_no == "01.01.01")
    spec = PartSpec.model_validate_json(
        (RECZNE / "01.01.01.json").read_text(encoding="utf-8")
    )
    prompt = build_user_prompt(korona, attempt=1)
    assert "D1" in prompt and "2xØ10" in prompt

    cache = ResponseCache(tmp_path / "cache")
    assert cache.load(korona, prompt, "m1") is None
    cache.save(korona, prompt, spec, "m1")
    assert cache.load(korona, prompt, "m1") == spec
    # different model or different prompt -> miss (no silent reuse)
    assert cache.load(korona, prompt, "m2") is None
    assert cache.load(korona, prompt + " ", "m1") is None


def test_offline_mode_does_not_read_manual_specs(tmp_path):
    """Manual specs named <nr>.json must never be served as cached AI answers."""
    doc = extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf")
    korona = next(p for p in doc.parts if p.drawing_no == "01.01.01")
    interp = LLMInterpreter(provider="deepseek", model="x", cache_dir=RECZNE, offline_only=True)
    import pytest
    with pytest.raises(FatalInterpreterError):
        interp.interpret(korona)


def test_fatal_interpreter_error_is_not_retried(tmp_path):
    """Missing key / offline miss / truncated answer: one attempt, not three paid ones."""
    doc = extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf")
    part = next(p for p in doc.parts if p.drawing_no == "01.01.02")

    class _Fatal:
        calls = 0

        def interpret(self, part, feedback_errors=None, attempt=1):
            _Fatal.calls += 1
            raise FatalInterpreterError("Brak klucza DEEPSEEK_API_KEY")

    report, spec = process_part(part, _Fatal(), out_dir=tmp_path, max_attempts=3)
    assert _Fatal.calls == 1
    assert report.status == "BŁĄD" and spec is None
    assert any("nie ponawiam" in e for e in report.errors)


def test_part_without_drawing_geometry_is_at_most_uncertain(tmp_path):
    doc = extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf")
    part = next(p for p in doc.parts if p.drawing_no == "01.01.02")
    report, _ = process_part(part, ManualSpecInterpreter(RECZNE), out_dir=tmp_path)
    assert report.status == "NIEPEWNE"


def test_end_to_end_offline_pipeline_and_cli(tmp_path, capsys):
    out_dir = tmp_path / "wyniki"
    rc = main(
        [
            str(REPO_ROOT / "rysunki" / "cad_07_1.pdf"),
            "--reczne",
            str(RECZNE),
            "--out",
            str(out_dir),
        ]
    )
    assert rc == 0
    out_text = capsys.readouterr().out
    assert "01.01.01" in out_text
    assert "01.01.06" in out_text
    assert "NIEPEWNE" in out_text

    # Verify all 6 STEP files, HTML report, and JSON report exist
    step_files = sorted(out_dir.glob("*.step"))
    assert len(step_files) == 6
    html_report = out_dir / "raport.html"
    assert html_report.is_file()
    html_text = html_report.read_text(encoding="utf-8")
    assert "Korona" in html_text
    assert "NIEPEWNE" in html_text
    assert "M10 LH" in html_text
    assert "data:image/png;base64," in html_text
    assert (out_dir / "raport.json").is_file()


def test_cli_extract_only(tmp_path, capsys):
    out_dir = tmp_path / "ext"
    rc = main([str(REPO_ROOT / "rysunki" / "cad_08_1.pdf"), "--extract-only", "--out", str(out_dir)])
    assert rc == 0
    assert "8 pozycji BOM" in capsys.readouterr().out
    assert (out_dir / "ekstrakcja.json").is_file()


def test_process_pdf_with_part_filter(tmp_path):
    interp = ManualSpecInterpreter(RECZNE)
    res = process_pdf(
        REPO_ROOT / "rysunki" / "cad_07_1.pdf",
        out_dir=tmp_path / "filtered",
        interpreter=interp,
        part_filter={"01.01.02", "01.01.04"},
    )
    assert [r.drawing_no for r in res.reports] == ["01.01.02", "01.01.04"]
    assert all(r.status == "OK" for r in res.reports)
