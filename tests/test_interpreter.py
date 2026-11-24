"""LLM interpreter tests with a fake OpenAI-compatible client (no network, no cost)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from drawing2cad.interpreter import LLMInterpreter
from drawing2cad.pdf_extract import extract_pdf

REPO_ROOT = Path(__file__).resolve().parent.parent
RECZNE = REPO_ROOT / "tests" / "fixtures" / "reczne"


class _FakeCompletions:
    def __init__(self, reply: str, finish_reason: str = "stop") -> None:
        self.reply = reply
        self.finish_reason = finish_reason
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        msg = SimpleNamespace(content=self.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=self.finish_reason)])


def _fake_client(reply: str, finish_reason: str = "stop"):
    comp = _FakeCompletions(reply, finish_reason)
    return SimpleNamespace(chat=SimpleNamespace(completions=comp)), comp


@pytest.fixture(scope="module")
def podstawa(tmp_path_factory):
    doc = extract_pdf(REPO_ROOT / "rysunki" / "cad_07_1.pdf",
                      render_dir=tmp_path_factory.mktemp("img"))
    return next(p for p in doc.parts if p.drawing_no == "01.01.02")


def _interp(tmp_path, vision=True):
    return LLMInterpreter(provider="deepseek", model="test-model", api_key="k",
                          cache_dir=tmp_path / "cache", vision=vision)


def test_deepseek_request_has_image_json_mode_and_is_cached(tmp_path, podstawa):
    reply = (RECZNE / "01.01.02.json").read_text(encoding="utf-8")
    interp = _interp(tmp_path)
    interp._client, comp = _fake_client("```json\n" + reply + "\n```")
    spec = interp.interpret(podstawa)
    assert spec.drawing_no == "01.01.02"
    call = comp.calls[0]
    assert call["model"] == "test-model"
    assert call["response_format"] == {"type": "json_object"}
    user = call["messages"][1]["content"]
    assert user[0]["type"] == "image_url"
    assert user[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "D1" in user[1]["text"]
    # second call served from cache, no new request
    assert interp.interpret(podstawa) == spec
    assert len(comp.calls) == 1


def test_without_image_part_is_flagged_uncertain(tmp_path, podstawa):
    reply = (RECZNE / "01.01.02.json").read_text(encoding="utf-8")
    interp = _interp(tmp_path, vision=False)
    interp._client, comp = _fake_client(reply)
    spec = interp.interpret(podstawa)
    assert all(c["type"] == "text" for c in comp.calls[0]["messages"][1]["content"])
    assert any(a.id == "AI-BEZ-OBRAZU" and a.severity == "ostrzezenie" for a in spec.ambiguities)


def test_invalid_json_from_ai_raises_clear_error(tmp_path, podstawa):
    interp = _interp(tmp_path)
    interp._client, _ = _fake_client(json.dumps({"drawing_no": "x"}))
    with pytest.raises(RuntimeError, match="niezgodna ze schematem"):
        interp.interpret(podstawa)


def test_missing_key_gives_polish_message(tmp_path, podstawa, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    interp = LLMInterpreter(provider="deepseek", model="m", api_key="", vision=True)
    interp._api_key = ""
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        interp.interpret(podstawa)


def test_truncated_answer_says_token_limit(tmp_path, podstawa):
    interp = _interp(tmp_path)
    interp._client, _ = _fake_client('{"drawing_no": "01.0', finish_reason="length")
    with pytest.raises(RuntimeError, match="LLM_MAX_TOKENS"):
        interp.interpret(podstawa)


def test_changing_system_prompt_invalidates_cache(tmp_path, podstawa, monkeypatch):
    """Regression: an edited system prompt must not replay answers to the old prompt."""
    import drawing2cad.interpreter as mod
    reply = (RECZNE / "01.01.02.json").read_text(encoding="utf-8")
    interp = _interp(tmp_path)
    interp._client, comp = _fake_client(reply)
    interp.interpret(podstawa)
    monkeypatch.setattr(mod, "SYSTEM_PROMPT", mod.SYSTEM_PROMPT + " 7. Nowa zasada.")
    interp.interpret(podstawa)
    assert len(comp.calls) == 2
