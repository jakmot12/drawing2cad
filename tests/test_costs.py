"""Cost control: tokens and cost are counted, the budget stops calls, paid runs need consent."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from drawing2cad.cli import main
from drawing2cad.interpreter import FatalInterpreterError, LLMInterpreter
from drawing2cad.pdf_extract import extract_pdf

REPO_ROOT = Path(__file__).resolve().parent.parent
RECZNE = REPO_ROOT / "tests" / "fixtures" / "reczne"
PDF = REPO_ROOT / "rysunki" / "cad_07_1.pdf"


@pytest.fixture(scope="module")
def podstawa():
    return next(p for p in extract_pdf(PDF).parts if p.drawing_no == "01.01.02")


def _client(reply: str, completion_tokens: int, calls: list):
    def create(**kw):
        calls.append(kw)
        usage = SimpleNamespace(prompt_tokens=7225, completion_tokens=completion_tokens, prompt_cache_hit_tokens=7040,
                                completion_tokens_details=SimpleNamespace(reasoning_tokens=completion_tokens - 800))
        msg = SimpleNamespace(content=reply)
        return SimpleNamespace(usage=usage, choices=[SimpleNamespace(message=msg, finish_reason="stop")])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_cost_is_counted_and_logged(tmp_path, podstawa):
    calls: list = []
    interp = LLMInterpreter(provider="deepseek", model="m", api_key="k", vision=False, cache_dir=tmp_path, max_cost=1.0)
    interp._client = _client((RECZNE / "01.01.02.json").read_text(encoding="utf-8"), 7783, calls)
    interp.interpret(podstawa)
    c = interp.costs.calls[0]
    assert (c["tokeny_wejscie"], c["tokeny_wejscie_cache"], c["tokeny_wyjscie"]) == (7225, 7040, 7783)
    assert c["usd"] == pytest.approx((185 * 0.30 + 7040 * 0.006 + 7783 * 1.20) / 1e6, rel=1e-3)
    ledger = (tmp_path / "koszty.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(ledger[0])["czesc"] == "01.01.02"
    interp.interpret(podstawa)  # cache hit: free, not logged
    assert len(interp.costs.calls) == 1 and len(calls) == 1


def test_budget_stops_before_overspending(tmp_path, podstawa):
    calls: list = []
    interp = LLMInterpreter(provider="deepseek", model="m", api_key="k", vision=False, max_cost=0.085)
    interp._client = _client((RECZNE / "01.01.02.json").read_text(encoding="utf-8"), 30000, calls)
    interp.interpret(podstawa, attempt=1)  # first call allowed (worst case 0.083), costs ~0.036; next: 0.036 + 0.05 > 0.085
    with pytest.raises(FatalInterpreterError, match="Limit kosztów"):
        interp.interpret(podstawa, feedback_errors=["x"], attempt=2)
    assert len(calls) == 1


def test_paid_run_needs_consent(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    rc = main([str(PDF), "--out", str(tmp_path / "w"), "--parts", "01.01.02"])
    err = capsys.readouterr()
    assert rc == 1
    assert "Najgorszy przypadek" in err.out and "--yes" in err.err
    assert not (tmp_path / "w").exists()
