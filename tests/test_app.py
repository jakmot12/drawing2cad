"""Desktop app backend (FastAPI): offline only, no network and no paid AI calls."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from drawing2cad.app.jobs import (  # noqa: E402
    CANCELLED_MSG,
    AppConfig,
    Job,
    JobManager,
    _ProgressInterpreter,
)
from drawing2cad.app.server import create_app  # noqa: E402
from drawing2cad.interpreter import FatalInterpreterError, LLMInterpreter  # noqa: E402
from drawing2cad.pdf_extract import extract_pdf  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PDF = REPO_ROOT / "rysunki" / "cad_07_1.pdf"
H = {"X-Drawing2cad": "1"}


@pytest.fixture(autouse=True)
def _no_paid_api(monkeypatch):
    """Any attempt to reach a real AI API fails the test."""

    def boom(*_a, **_k):
        raise AssertionError("płatne wywołanie API w teście")

    monkeypatch.setattr(LLMInterpreter, "_call_openai_compat", boom)
    monkeypatch.setattr(LLMInterpreter, "_call_anthropic", boom)


@pytest.fixture
def app_env(tmp_path):
    opened: list[tuple[Path, bool]] = []
    cfg = AppConfig(work_dir=tmp_path / "work", ai_cache_dir=tmp_path / "cache", decisions_dir=tmp_path / "decyzje")
    app = create_app(cfg, opener=lambda p, sel=False: opened.append((p, sel)),
                     allowed_hosts={"127.0.0.1", "localhost", "testserver"})
    client = TestClient(app)
    return client, app.state.manager, opened, cfg


def test_guard_requires_app_header_and_local_host(app_env):
    client, *_ = app_env
    assert client.post("/api/samples/cad_07_1.pdf").status_code == 403
    assert client.get("/api/info", headers={"host": "evil.example"}).status_code == 403
    assert client.get("/api/info").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/static/vendor/three/three.module.min.js").status_code == 200


def test_info_and_pdf_selection(app_env):
    client, *_ = app_env
    info = client.get("/api/info").json()
    assert {s["name"] for s in info["samples"]} >= {"cad_07_1.pdf", "cad_08_1.pdf"}
    assert all(m["available"] for m in info["modes"])

    r = client.post("/api/samples/cad_07_1.pdf", headers=H).json()
    assert [p["drawing_no"] for p in r["parts"]] == [f"01.01.0{i}" for i in range(1, 7)]
    asm = client.post("/api/samples/cad_08_1.pdf", headers=H).json()
    assert asm["is_assembly"] and len(asm["bom"]) == 8
    assert client.post("/api/samples/..%2Fpyproject.toml", headers=H).status_code == 404

    bad = client.post("/api/upload", headers=H, files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert bad.status_code == 400
    up = client.post("/api/upload", headers=H, files={"file": ("rys.pdf", PDF.read_bytes(), "application/pdf")})
    assert up.status_code == 200 and len(up.json()["parts"]) == 6


def test_paid_run_requires_explicit_cost_confirmation(app_env):
    client, manager, *_ = app_env
    pdf_id = client.post("/api/samples/cad_07_1.pdf", headers=H).json()["id"]
    base = {"pdf_id": pdf_id, "mode": "ai", "max_cost": 0.3}
    assert client.post("/api/jobs", headers=H, json=base).status_code == 403
    r = client.post("/api/jobs", headers=H, json=base | {"confirm_cost": True, "accepted_max_cost": 0.5})
    assert r.status_code == 403
    r = client.post("/api/jobs", headers=H, json=base | {"confirm_cost": True, "accepted_max_cost": None})
    assert r.status_code == 403
    assert manager.jobs == {}

    est = client.post("/api/estimate", headers=H, json={"pdf_id": pdf_id, "max_cost": 0.3, "max_attempts": 3}).json()
    assert est["n_parts"] == 6 and est["max_attempts"] == 3
    assert est["worst_usd"] == pytest.approx(min(6 * 3 * est["per_call_usd"], 0.3))
    assert est["max_cost"] == pytest.approx(0.3)
    sub = client.post("/api/estimate", headers=H, json={"pdf_id": pdf_id, "max_cost": 5, "parts": ["01.01.02"]}).json()
    assert sub["n_parts"] == 1


def test_cancel_stops_before_calling_interpreter():
    part = extract_pdf(PDF).parts[0]

    class _Inner:
        calls = 0

        def interpret(self, *_a, **_k):
            _Inner.calls += 1

    job = Job(id="j", mode="ai", pdf_name="x.pdf", pdf_path=PDF, out_dir=Path("."), max_attempts=3,
              part_filter=None, parts=[{"drawing_no": part.drawing_no, "name": part.name, "state": "pending"}])
    job.cancel_requested = True
    with pytest.raises(FatalInterpreterError, match="Przerwano"):
        _ProgressInterpreter(_Inner(), job, threading.Lock()).interpret(part)
    assert _Inner.calls == 0 and CANCELLED_MSG.startswith("Przerwano")


def test_demo_offline_run_end_to_end(app_env):
    client, manager, opened, cfg = app_env
    pdf_id = client.post("/api/samples/cad_07_1.pdf", headers=H).json()["id"]
    job = client.post("/api/jobs", headers=H, json={"pdf_id": pdf_id, "mode": "demo"}).json()
    assert job["status"] in ("queued", "running")
    assert client.post("/api/jobs", headers=H, json={"pdf_id": pdf_id, "mode": "demo"}).status_code == 409
    manager.wait(timeout=600)

    d = client.get(f"/api/jobs/{job['id']}").json()
    assert d["status"] == "done", d["error"]
    assert d["counts"] == {"OK": 4, "NIEPEWNE": 1, "BŁĄD": 1}
    assert d["costs"]["calls"] == 0 and d["costs"]["usd"] == 0
    assert "0 wywołań" in d["costs"]["summary"]
    assert all(p["state"] == "done" for p in d["parts"])
    reports = d["result"]["reports"]
    korona = reports[0]
    assert korona["status"] == "NIEPEWNE"
    assert {a["source"] for a in korona["ambiguities"]} == {"ai", "program"}

    base = f"/api/jobs/{job['id']}/parts/0"
    img = client.get(f"{base}/image")
    assert img.status_code == 200 and img.content.startswith(b"\x89PNG")
    stl = client.get(f"{base}/model.stl")
    assert stl.status_code == 200 and len(stl.content) > 1000
    edges = client.get(f"{base}/edges.json").json()
    assert edges["edges"] and len(edges["bbox"]) == 2
    step = client.get(f"{base}/model.step")
    assert step.status_code == 200 and b"ISO-10303" in step.content[:200]
    assert "attachment" in step.headers["content-disposition"]
    assert client.get(f"/api/jobs/{job['id']}/parts/99/image").status_code == 404
    assert client.get(f"/api/jobs/{job['id']}/report").status_code == 200

    rejected = next(r for r in reports if r["status"] == "BŁĄD")
    assert rejected["rejected_model"] and rejected["has_step"]

    assert client.post(f"/api/jobs/{job['id']}/open", headers=H, json={"what": "step_folder", "part": 1}).status_code == 200
    assert opened[-1][1] is True and opened[-1][0].suffix == ".step"

    # the run survives an app restart (history)
    again = JobManager(cfg)
    assert again.jobs[job["id"]].to_dict()["counts"] == {"OK": 4, "NIEPEWNE": 1, "BŁĄD": 1}

    # user decisions on assumptions (same store and rules as CLI --pytaj)
    url = f"/api/jobs/{job['id']}/decisions"
    pending = [a for a in korona["ambiguities_raw"] if a["blocking"]]
    assert pending and all(a["decision"] is None for a in pending)
    first = pending[0]["id"]
    d = client.post(url, headers=H, json={"part": 0, "ambiguity_id": first, "decision": "odrzucone", "comment": "zła oś"}).json()
    assert d["result"]["reports"][0]["status"] == "BŁĄD"
    assert any("zła oś" in e for e in d["result"]["reports"][0]["errors"])
    assert (cfg.decisions_dir / "cad_07_1.decyzje.json").is_file()
    import json as _json
    saved = _json.loads((Path(d["out_dir"]) / "raport.json").read_text(encoding="utf-8"))
    assert saved["reports"][0]["status"] == "BŁĄD"

    d = client.post(url, headers=H, json={"part": 0, "ambiguity_id": first, "decision": None}).json()
    assert d["result"]["reports"][0]["status"] == "NIEPEWNE"
    for a in pending:
        d = client.post(url, headers=H, json={"part": 0, "ambiguity_id": a["id"], "decision": "potwierdzone"}).json()
    k = d["result"]["reports"][0]
    assert k["status"] == "OK" and not k["errors"]
    assert all(a["decision"]["decyzja"] == "potwierdzone" for a in k["ambiguities_raw"] if a["blocking"])
    assert d["counts"] == {"OK": 5, "NIEPEWNE": 0, "BŁĄD": 1}
    bad = client.post(url, headers=H, json={"part": 0, "ambiguity_id": "NIE-MA", "decision": "potwierdzone"})
    assert bad.status_code == 404
