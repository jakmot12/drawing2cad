"""All stored real DeepSeek answers, re-verified with the current code at zero API cost.

No answer may be a silent error: a model that differs from the reference must get BŁĄD, or
NIEPEWNE with a reason found by the program itself (AI remarks alone do not count).
Parts whose drawing really is ambiguous (Korona: hole positions not dimensioned) must never be OK.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from benchmark_offline import DEFAULT_ANSWERS, evaluate_file  # noqa: E402

ANSWERS = sorted(DEFAULT_ANSWERS.glob("*.json"))


def test_enough_real_answers_are_stored():
    assert len(ANSWERS) >= 30


@pytest.mark.parametrize("answer", ANSWERS, ids=[a.stem for a in ANSWERS])
def test_real_ai_answer_is_never_a_silent_error(answer):
    row = evaluate_file(answer)
    assert row is not None
    assert not row.silent_error, (row.status, row.volume_diff, row.reasons[:3])
    if row.drawing_no == "01.01.01":  # hole positions are not dimensioned on the drawing
        assert row.status != "OK"
