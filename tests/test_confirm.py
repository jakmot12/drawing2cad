"""Rule 6: user confirms or rejects assumptions; decisions are tied to the exact assumption."""
from __future__ import annotations

from pathlib import Path

import pytest

from drawing2cad.blocks import build_part
from drawing2cad.confirm import CONFIRMED, REJECTED, DecisionStore, apply_decisions, ask_user, decision_keys
from drawing2cad.drawing_compare import extract_views
from drawing2cad.pdf_extract import extract_pdf
from drawing2cad.schema import PartSpec
from drawing2cad.verifier import verify_part

REPO_ROOT = Path(__file__).resolve().parent.parent
PDF = REPO_ROOT / "rysunki" / "cad_07_1.pdf"


@pytest.fixture(scope="module")
def korona_report():
    part = next(p for p in extract_pdf(PDF).parts if p.drawing_no == "01.01.01")
    spec = PartSpec.model_validate_json((REPO_ROOT / "tests" / "fixtures" / "reczne" / "01.01.01.json").read_text(encoding="utf-8"))
    report = verify_part(spec, build_part(spec), part.dimensions, views=extract_views(str(PDF), part.page_index, part.bbox))
    assert report.status == "NIEPEWNE"
    return report


def _answers(*values):
    it = iter(values)
    return lambda _prompt: next(it)


def test_confirming_every_assumption_gives_ok(tmp_path, korona_report):
    store = DecisionStore(tmp_path / "d.json")
    n_blocking = sum(a.severity in ("blad_rysunku", "ostrzezenie") for a in korona_report.ambiguities)
    asked = ask_user(korona_report, store, input_fn=_answers(*["t"] * n_blocking), print_fn=lambda _s: None)
    assert asked == n_blocking
    store.save()
    out = apply_decisions(korona_report, DecisionStore(tmp_path / "d.json"))  # reloaded from disk
    assert out.status == "OK"
    assert all("Potwierdzone przez użytkownika" in a.assumption for a in out.ambiguities if a.severity == "info"
               and a.id in {b.id for b in korona_report.ambiguities if b.severity != "info"})


def test_rejecting_an_assumption_gives_error(tmp_path, korona_report):
    store = DecisionStore(tmp_path / "d.json")
    key = next(k for a, k in zip(korona_report.ambiguities, decision_keys(korona_report))
               if a.severity in ("blad_rysunku", "ostrzezenie"))
    store.set_key(korona_report, key, REJECTED, "otwory są w x = ±12")
    out = apply_decisions(korona_report, store)
    assert out.status == "BŁĄD"
    assert any("odrzucił" in e and "±12" in e for e in out.errors)


def test_decision_does_not_carry_over_to_a_different_assumption(tmp_path, korona_report):
    store = DecisionStore(tmp_path / "d.json")
    for a in korona_report.ambiguities:
        store.set(korona_report.drawing_no, a, CONFIRMED)
    changed = korona_report.model_copy(update={"ambiguities": [
        a.model_copy(update={"assumption": a.assumption + " (inne założenie)"}) for a in korona_report.ambiguities
    ]})
    assert apply_decisions(changed, store).status == "NIEPEWNE"


def test_skipping_leaves_the_part_uncertain(tmp_path, korona_report):
    store = DecisionStore(tmp_path / "d.json")
    n_blocking = sum(a.severity in ("blad_rysunku", "ostrzezenie") for a in korona_report.ambiguities)
    ask_user(korona_report, store, input_fn=_answers(*[""] * n_blocking), print_fn=lambda _s: None)
    assert apply_decisions(korona_report, store).status == "NIEPEWNE"
