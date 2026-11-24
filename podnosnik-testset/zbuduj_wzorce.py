"""Buduje modele wzorcowe, eksportuje STEP i porównuje gabaryty z ground_truth.json."""
import json
import sys
from pathlib import Path

import cadquery as cq

sys.path.insert(0, str(Path(__file__).parent / "wzorce"))
from czesci import CZESCI  # noqa: E402

TOL = 0.01  # mm
gt = json.loads(Path("ground_truth.json").read_text(encoding="utf-8"))
blad = False
for czesc in gt["czesci"]:
    nr = czesc["nr_rysunku"]
    bryla = CZESCI[nr]()
    solid = bryla.val()
    bb = solid.BoundingBox()
    gab = sorted([bb.xlen, bb.ylen, bb.zlen], reverse=True)
    ocz = sorted(czesc["gabaryty_mm"], reverse=True)
    ok = all(abs(a - b) < TOL for a, b in zip(gab, ocz)) and solid.isValid()
    blad |= not ok
    plik = Path("step") / f"{nr}_{czesc['nazwa_plik']}.step"
    cq.exporters.export(bryla, str(plik))
    print(f"{'OK ' if ok else 'BŁĄD'} {nr} {czesc['nazwa']:<9} gabaryty {[round(g, 2) for g in gab]} "
          f"oczekiwane {ocz}  objętość {solid.Volume():.1f} mm³  -> {plik.name}")
sys.exit(1 if blad else 0)
