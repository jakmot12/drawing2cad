"""Test: wzorce obrócone i przesunięte losowo muszą wyjść ZGODNE, zmienione NIEZGODNE."""
import random
import sys

import cadquery as cq

sys.path.insert(0, "wzorce")
from czesci import CZESCI, korona, ramie  # noqa: E402
from porownaj import TOL_OBJ, porownaj  # noqa: E402

random.seed(1)


def losowo(b):
    s = b.val()
    s = s.rotate(cq.Vector(), cq.Vector(1, 0.3, 0.2), random.uniform(0, 360))
    s = s.rotate(cq.Vector(), cq.Vector(0, 1, 0.5), random.uniform(0, 360))
    return s.translate(cq.Vector(*[random.uniform(-200, 200) for _ in range(3)]))


def test(nazwa, wz, md, oczekiwany):
    n, b = porownaj(wz, md)
    wynik = "ZGODNY" if n < TOL_OBJ and b < TOL_OBJ else "NIEZGODNY"
    znak = "✓" if wynik == oczekiwany else "✗ BŁĄD TESTU"
    print(f"{znak} {nazwa:<40} nadmiar {n:7.1f}  brak {b:7.1f}  -> {wynik}")
    return wynik == oczekiwany


ok = True
for nr, f in CZESCI.items():
    ok &= test(f"{nr} {f.__name__}, obrócony i przesunięty", f().val(), losowo(f()), "ZGODNY")
ok &= test("Ramię: rozstaw 124 zamiast 125", ramie().val(), losowo(ramie(124)), "NIEZGODNY")
k = korona().cut(cq.Workplane("XZ").pushPoints([(-15, 10)]).circle(5.5).extrude(20, both=True))
ok &= test("Korona: jeden otwór Ø11 zamiast Ø10", korona().val(), losowo(k), "NIEZGODNY")
sys.exit(0 if ok else 1)
