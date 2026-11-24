"""Two ambiguities with the same id in one part never share one user decision (CLI and app)."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from drawing2cad.confirm import (
    CONFIRMED, REJECTED, DecisionStore, apply_decisions, ask_user, decision_keys, model_fingerprint,
)
from drawing2cad.schema import Ambiguity, DimensionCheck, VerificationReport


def _amb(desc: str, assumption: str) -> Ambiguity:
    return Ambiguity(id="AI-OTWOR", description=desc, assumption=assumption, severity="ostrzezenie")


def _report(*ambs: Ambiguity) -> VerificationReport:
    return VerificationReport(drawing_no="X1", name="Część", status="NIEPEWNE", passed=True,
                              bounding_box_mm=(10.0, 20.0, 30.0), volume_mm3=1000.0, ambiguities=list(ambs))


DIFFERENT = (_amb("Otwór A bez położenia.", "Na osi."), _amb("Otwór B bez głębokości.", "Przelotowy."))
IDENTICAL = (_amb("Otwór bez położenia.", "Na osi."), _amb("Otwór bez położenia.", "Na osi."))


def _answers(*values):
    it = iter(values)
    return lambda _prompt: next(it)


@pytest.mark.parametrize("ambs", [DIFFERENT, IDENTICAL], ids=["rozne", "identyczne"])
def test_cli_each_entry_gets_its_own_decision(tmp_path, ambs):
    report = _report(*ambs)
    store = DecisionStore(tmp_path / "d.json")
    # reject the first, confirm the second
    assert ask_user(report, store, input_fn=_answers("n", "złe", "t"), print_fn=lambda _s: None) == 2
    store.save()
    store = DecisionStore(tmp_path / "d.json")
    assert len(store.data) == 2
    out = apply_decisions(report, store)
    assert out.status == "BŁĄD"
    assert out.ambiguities[0].severity == "ostrzezenie"  # rejected stays blocking
    assert out.ambiguities[1].severity == "info" and "Potwierdzone" in out.ambiguities[1].assumption
    assert sum("odrzucił" in e for e in out.errors) == 1
    # nothing left to ask
    assert ask_user(report, store, input_fn=_answers(), print_fn=lambda _s: None) == 0


def test_keys_are_unique_and_stable_between_runs():
    from drawing2cad.confirm import decision_keys

    a = decision_keys(_report(*IDENTICAL))
    b = decision_keys(_report(*(x.model_copy() for x in IDENTICAL)))
    assert a == b and len(set(a)) == 2


def test_unique_ids_keep_the_old_key(tmp_path):
    """Decision files written before the fix (one entry per ambiguity key) still apply."""
    report = _report(DIFFERENT[0])
    store = DecisionStore(tmp_path / "d.json")
    store.set(report.drawing_no, report.ambiguities[0], CONFIRMED, model=model_fingerprint(report))  # old API / format
    assert apply_decisions(report, store).status == "OK"
    assert ask_user(report, store, input_fn=_answers(), print_fn=lambda _s: None) == 0


def test_old_shared_decision_for_identical_duplicates_is_not_applied_silently(tmp_path):
    """An old file has one decision for the shared key: it is ambiguous now, so both entries are asked again."""
    report = _report(*IDENTICAL)
    store = DecisionStore(tmp_path / "d.json")
    store.set(report.drawing_no, report.ambiguities[0], REJECTED, model=model_fingerprint(report))  # old format
    out = apply_decisions(report, store)
    assert out.status == "NIEPEWNE" and not out.errors
    lines: list[str] = []
    assert ask_user(report, store, input_fn=_answers("t", "t"), print_fn=lines.append) == 2
    assert any("kilku" in s for s in lines)  # the user is told why they are asked again
    assert apply_decisions(report, store).status == "OK"


# ----------------------------------------------------------------- app API
fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from drawing2cad.app.jobs import AppConfig, Job  # noqa: E402
from drawing2cad.app.server import create_app  # noqa: E402

H = {"X-Drawing2cad": "1"}


def _app_with_job(tmp_path, report: VerificationReport):
    cfg = AppConfig(work_dir=tmp_path / "work", ai_cache_dir=tmp_path / "cache", decisions_dir=tmp_path / "decyzje")
    app = create_app(cfg, opener=lambda *_a, **_k: None, allowed_hosts={"testserver"})
    job = Job(id="j1", mode="demo", pdf_name="x.pdf", pdf_path=tmp_path / "x.pdf", out_dir=tmp_path / "out",
              max_attempts=1, part_filter=None, parts=[], status="done",
              result={"reports": [report.model_dump()], "raw_reports": [report.model_dump()], "bom": []},
              decisions_path=tmp_path / "decyzje" / "x.decyzje.json")
    job.out_dir.mkdir(parents=True)
    app.state.manager.jobs[job.id] = job
    return TestClient(app), job


def _raw(client, job):
    return client.get(f"/api/jobs/{job.id}").json()["result"]["reports"][0]["ambiguities_raw"]


@pytest.mark.parametrize("ambs", [DIFFERENT, IDENTICAL], ids=["rozne", "identyczne"])
def test_app_rejecting_one_entry_does_not_touch_the_other(tmp_path, ambs):
    client, job = _app_with_job(tmp_path, _report(*ambs))
    url = f"/api/jobs/{job.id}/decisions"
    keys = [a.get("key") for a in _raw(client, job)]
    assert None not in keys and len(set(keys)) == 2
    d = client.post(url, headers=H, json={"part": 0, "ambiguity_id": "AI-OTWOR", "ambiguity_key": keys[1],
                                          "decision": "odrzucone", "comment": "zły"}).json()
    r = d["result"]["reports"][0]
    assert r["ambiguities_raw"][0]["decision"] is None
    assert r["ambiguities_raw"][1]["decision"]["decyzja"] == "odrzucone"
    assert r["status"] == "BŁĄD"
    d = client.post(url, headers=H, json={"part": 0, "ambiguity_id": "AI-OTWOR", "ambiguity_key": keys[0],
                                          "decision": "potwierdzone"}).json()
    r = d["result"]["reports"][0]
    assert [a["decision"]["decyzja"] for a in r["ambiguities_raw"]] == ["potwierdzone", "odrzucone"]
    # withdrawing one leaves the other
    d = client.post(url, headers=H, json={"part": 0, "ambiguity_id": "AI-OTWOR", "ambiguity_key": keys[1],
                                          "decision": None}).json()
    r = d["result"]["reports"][0]
    assert r["ambiguities_raw"][0]["decision"]["decyzja"] == "potwierdzone"
    assert r["ambiguities_raw"][1]["decision"] is None
    assert len(json.loads(Path(job.decisions_path).read_text(encoding="utf-8"))) == 1


def test_app_id_alone_is_refused_when_it_is_ambiguous(tmp_path):
    client, job = _app_with_job(tmp_path, _report(*DIFFERENT))
    res = client.post(f"/api/jobs/{job.id}/decisions", headers=H,
                      json={"part": 0, "ambiguity_id": "AI-OTWOR", "decision": "odrzucone"})
    assert res.status_code == 409
    assert not Path(job.decisions_path).exists()


def test_app_id_alone_still_works_when_unique(tmp_path):
    client, job = _app_with_job(tmp_path, _report(DIFFERENT[0]))
    d = client.post(f"/api/jobs/{job.id}/decisions", headers=H,
                    json={"part": 0, "ambiguity_id": "AI-OTWOR", "decision": "potwierdzone"}).json()
    assert d["result"]["reports"][0]["status"] == "OK"


def test_app_unknown_key_is_404(tmp_path):
    client, job = _app_with_job(tmp_path, _report(*DIFFERENT))
    res = client.post(f"/api/jobs/{job.id}/decisions", headers=H,
                      json={"part": 0, "ambiguity_id": "AI-OTWOR", "ambiguity_key": "X1|AI-OTWOR|zly", "decision": "odrzucone"})
    assert res.status_code == 404


def test_app_marks_old_shared_decision_as_ambiguous(tmp_path):
    report = _report(*IDENTICAL)
    client, job = _app_with_job(tmp_path, report)
    store = DecisionStore(job.decisions_path)
    store.set(report.drawing_no, report.ambiguities[0], CONFIRMED, model=model_fingerprint(report))  # old format
    store.save()
    r = client.get(f"/api/jobs/{job.id}").json()["result"]["reports"][0]
    assert r["status"] == "NIEPEWNE"
    assert all(a["decision"] is None and a.get("decision_ambiguous") for a in r["ambiguities_raw"])


# ------------------------------------------------- stale / foreign decisions never give OK
def _amb_d(desc: str, assumption: str, dims: list[str]) -> Ambiguity:
    return Ambiguity(id="AI-OTWOR", description=desc, assumption=assumption, severity="ostrzezenie",
                     affected_dimensions=dims)


def _report_checks(*ambs: Ambiguity, dims=("D1", "D2")) -> VerificationReport:
    checks = [DimensionCheck(dim_id=d, raw_text=d, target="x", expected="1", measured="1", status="NIEPEWNE")
              for d in dims]
    return _report(*ambs).model_copy(update={"checks": checks})


def test_legacy_decision_does_not_revive_when_copies_drop_to_one(tmp_path):
    """Old shared decision 'confirmed', user rejects #2, next run has one copy: never a silent OK."""
    two = _report(*IDENTICAL)
    store = DecisionStore(tmp_path / "d.json")
    store.set(two.drawing_no, two.ambiguities[0], CONFIRMED, model=model_fingerprint(two))  # old format
    store.set_key(two, decision_keys(two)[1], REJECTED)
    store.save()
    store = DecisionStore(tmp_path / "d.json")
    one = _report(IDENTICAL[0])
    assert apply_decisions(one, store).status != "OK"
    (_k, d, ambiguous), = store.resolve(one)
    assert d is None and ambiguous


def test_swapped_entries_with_different_dimensions_keep_their_decisions(tmp_path):
    a1 = _amb_d("Otwór bez położenia.", "Na osi.", ["D1"])
    a2 = _amb_d("Otwór bez położenia.", "Na osi.", ["D2"])
    first = _report_checks(a1, a2)
    store = DecisionStore(tmp_path / "d.json")
    store.set_key(first, decision_keys(first)[0], CONFIRMED)  # confirms the D1 entry
    out = apply_decisions(first, store)
    assert {c.dim_id: c.status for c in out.checks} == {"D1": "OK", "D2": "NIEPEWNE"}
    out = apply_decisions(_report_checks(a2, a1), store)
    assert {c.dim_id: c.status for c in out.checks} == {"D1": "OK", "D2": "NIEPEWNE"}
    assert out.status == "NIEPEWNE"


def test_dimension_order_does_not_change_the_key():
    a = _amb_d("Otwór.", "Na osi.", ["D2", "D1"])
    b = _amb_d("Otwór.", "Na osi.", ["D1", "D2"])
    assert decision_keys(_report(a)) == decision_keys(_report(b))


def test_old_decision_for_entry_with_dimensions_is_asked_again(tmp_path):
    """Old files were keyed without affected dimensions: such a decision is not applied silently."""
    a1 = _amb_d("Otwór bez położenia.", "Na osi.", ["D1"])
    report = _report_checks(a1, dims=("D1",))
    store = DecisionStore(tmp_path / "d.json")
    store.set(report.drawing_no, a1, CONFIRMED, model=model_fingerprint(report))  # old format, no dimensions
    assert apply_decisions(report, store).status == "NIEPEWNE"
    (_k, d, ambiguous), = store.resolve(report)
    assert d is None and ambiguous


def test_old_keyed_decision_is_not_moved_by_an_info_entry(tmp_path):
    """A per-copy decision from the previous key scheme (info entries were counted) is not reassigned."""
    block = _amb("Otwór bez położenia.", "Na osi.")
    info = block.model_copy(update={"severity": "info"})
    report = _report(info, block, block.model_copy())
    fp = model_fingerprint(report)
    store = DecisionStore(tmp_path / "d.json")
    base = f"{report.drawing_no}|{block.id}|" + __import__("hashlib").sha1(
        f"{block.description}\n{block.assumption}\n{fp}".encode()).hexdigest()[:12]
    # previous scheme: info = base, blocking copies = base#2, base#3; the user confirmed base#2 only
    store.data[base + "#2"] = {"decyzja": CONFIRMED, "czas": "t", "komentarz": "", "klucz": base + "#2"}
    res = store.resolve(report)
    assert all(d is None for _k, d, _a in res)
    assert res[1][2] and res[2][2]
    assert apply_decisions(report, store).status == "NIEPEWNE"


def test_info_entry_does_not_shift_the_key_of_a_blocking_one(tmp_path):
    block = _amb("Otwór bez położenia.", "Na osi.")
    info = block.model_copy(update={"severity": "info"})
    alone = _report(block)
    with_info = _report(info, block)
    assert decision_keys(with_info)[1] == decision_keys(alone)[0]
    assert len(set(decision_keys(with_info))) == 2
    # an old-format decision for the only blocking entry still applies
    store = DecisionStore(tmp_path / "d.json")
    store.set(alone.drawing_no, block, CONFIRMED, model=model_fingerprint(alone))
    res = store.resolve(with_info)
    assert res[1][1] is not None and not res[1][2]
    assert res[0][1] is None and not res[0][2]
    assert apply_decisions(with_info, store).status == "OK"
    # a new decision as well
    store = DecisionStore(tmp_path / "e.json")
    store.set_key(alone, decision_keys(alone)[0], REJECTED)
    assert apply_decisions(with_info, store).status == "BŁĄD"


def test_three_identical_entries_middle_one_independently(tmp_path):
    report = _report(*(IDENTICAL[0].model_copy() for _ in range(3)))
    keys = decision_keys(report)
    assert len(set(keys)) == 3
    store = DecisionStore(tmp_path / "d.json")

    def decisions():
        return [d["decyzja"] if d else None for _k, d, _a in store.resolve(report)]

    store.set_key(report, keys[1], CONFIRMED)
    assert decisions() == [None, CONFIRMED, None]
    store.set_key(report, keys[0], REJECTED)
    store.set_key(report, keys[2], CONFIRMED)
    assert decisions() == [REJECTED, CONFIRMED, CONFIRMED]
    store.set_key(report, keys[1], REJECTED)
    assert decisions() == [REJECTED, REJECTED, CONFIRMED]
    store.set_key(report, keys[1], None)  # withdraw the middle one only
    assert decisions() == [REJECTED, None, CONFIRMED]
    out = apply_decisions(report, store)
    assert out.status == "BŁĄD"
    assert [a.severity for a in out.ambiguities] == ["ostrzezenie", "ostrzezenie", "info"]


def test_app_partial_resolution_of_legacy_decision(tmp_path):
    report = _report(*IDENTICAL)
    client, job = _app_with_job(tmp_path, report)
    store = DecisionStore(job.decisions_path)
    store.set(report.drawing_no, report.ambiguities[0], CONFIRMED, model=model_fingerprint(report))  # old format
    store.save()
    url = f"/api/jobs/{job.id}/decisions"
    keys = [a["key"] for a in _raw(client, job)]
    r = client.post(url, headers=H, json={"part": 0, "ambiguity_key": keys[1], "decision": "odrzucone"}).json()
    raw = r["result"]["reports"][0]["ambiguities_raw"]
    assert raw[0]["decision"] is None and raw[0]["decision_ambiguous"] is True
    assert raw[1]["decision"]["decyzja"] == "odrzucone" and not raw[1]["decision_ambiguous"]
    r = client.post(url, headers=H, json={"part": 0, "ambiguity_key": keys[0], "decision": "potwierdzone"}).json()
    raw = r["result"]["reports"][0]["ambiguities_raw"]
    assert [a["decision"]["decyzja"] for a in raw] == ["potwierdzone", "odrzucone"]
    assert not any(a["decision_ambiguous"] for a in raw)


def test_app_needs_id_or_key(tmp_path):
    client, job = _app_with_job(tmp_path, _report(DIFFERENT[0]))
    res = client.post(f"/api/jobs/{job.id}/decisions", headers=H, json={"part": 0, "decision": "potwierdzone"})
    assert 400 <= res.status_code < 500
    assert not Path(job.decisions_path).exists()


def test_app_key_of_info_entry_is_404(tmp_path):
    info = _amb("Uwaga ogólna.", "Brak.").model_copy(update={"severity": "info"})
    client, job = _app_with_job(tmp_path, _report(info, DIFFERENT[0]))
    key = _raw(client, job)[0]["key"]
    res = client.post(f"/api/jobs/{job.id}/decisions", headers=H,
                      json={"part": 0, "ambiguity_key": key, "decision": "potwierdzone"})
    assert res.status_code == 404
    assert not Path(job.decisions_path).exists()


def test_app_old_run_without_raw_reports_keeps_decisions(tmp_path):
    report = _report(*DIFFERENT)
    client, job = _app_with_job(tmp_path, report)
    del job.result["raw_reports"]  # run saved before the raw reports were kept
    key = decision_keys(report)[0]
    store = DecisionStore(job.decisions_path)
    store.set_key(report, key, CONFIRMED)
    store.save()
    for _ in range(2):
        raw = _raw(client, job)
        assert raw[0]["key"] == key and raw[0]["decision"]["decyzja"] == "potwierdzone"
    res = client.post(f"/api/jobs/{job.id}/decisions", headers=H,
                      json={"part": 0, "ambiguity_key": key, "decision": None})
    assert res.status_code == 200
    assert res.json()["result"]["reports"][0]["ambiguities_raw"][0]["decision"] is None


def test_parallel_decisions_are_both_saved(tmp_path, monkeypatch):
    report = _report(*IDENTICAL)
    client, job = _app_with_job(tmp_path, report)
    write_text = Path.write_text
    active: dict[str, int] = {}
    overlaps: list[str] = []

    def slow_write(self, *a, **k):  # slow writes; two writers of one file at once are recorded
        name = str(self)
        active[name] = active.get(name, 0) + 1
        if active[name] > 1:
            overlaps.append(self.name)
        try:
            threading.Event().wait(0.2 if self.name == "przebieg.json" else 0.0)
            return write_text(self, *a, **k)
        finally:
            active[name] -= 1

    monkeypatch.setattr(Path, "write_text", slow_write)
    manager = client.app.state.manager
    keys = decision_keys(report)
    errors: list[BaseException] = []

    for _ in range(5):
        Path(job.decisions_path).unlink(missing_ok=True)
        barrier = threading.Barrier(2)

        def run(key, decision, barrier=barrier):
            try:
                barrier.wait()
                manager.decide(job.id, 0, None, decision, amb_key=key)
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(keys[0], CONFIRMED)),
                   threading.Thread(target=run, args=(keys[1], REJECTED))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors and not overlaps
        saved = json.loads(Path(job.decisions_path).read_text(encoding="utf-8"))
        assert sorted(v["decyzja"] for v in saved.values()) == sorted([CONFIRMED, REJECTED])
        run_file = json.loads((job.out_dir / "przebieg.json").read_text(encoding="utf-8"))
        raw = run_file["result"]["reports"][0]["ambiguities_raw"]
        assert [a["decision"]["decyzja"] for a in raw] == [CONFIRMED, REJECTED]
