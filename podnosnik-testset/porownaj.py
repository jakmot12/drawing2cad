"""Porównuje model do sprawdzenia ze wzorcem (oba STEP).

Modele nie muszą mieć tego samego układu współrzędnych: oba są
przesuwane do środka masy i obracane do głównych osi bezwładności,
a potem sprawdzane są 24 możliwe orientacje i wybierana najlepsza.

Użycie: python porownaj.py wzorzec.step model.step
"""
import itertools
import sys

import cadquery as cq
import numpy as np
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Mat, gp_Quaternion, gp_Trsf, gp_Vec

TOL_OBJ = 1.0  # mm³, różnica objętości uznawana za zero


def wczytaj(p):
    return cq.importers.importStep(p).val()


def przeksztalc(s, r, t):
    """Obrót r (macierz 3x3) i przesunięcie t, odporne na błędy zaokrągleń."""
    tr = gp_Trsf()
    tr.SetRotation(gp_Quaternion(gp_Mat(*[float(v) for v in np.asarray(r).ravel()])))
    tr.SetTranslationPart(gp_Vec(*[float(v) for v in t]))
    return cq.Shape.cast(BRepBuilderAPI_Transform(s.wrapped, tr, True).Shape())


TOL_SYM = 0.005  # względna różnica momentów bezwładności uznawana za symetrię osiową


def do_osi_glownych(s):
    """Przesuwa bryłę do środka masy i obraca do głównych osi bezwładności.

    Zwraca bryłę i momenty główne (rosnąco)."""
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(s.wrapped, props)
    c = props.CentreOfMass()
    m = props.MatrixOfInertia()
    inercja = np.array([[m.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)])
    momenty, osie = np.linalg.eigh(inercja)  # kolumny = osie główne
    if np.linalg.det(osie) < 0:
        osie[:, 0] *= -1
    r = osie.T
    t = -r @ np.array([c.X(), c.Y(), c.Z()])
    return przeksztalc(s, r, t), momenty


def orientacje():
    """24 obroty zamieniające osie układu między sobą (bez odbić lustrzanych)."""
    for perm in itertools.permutations(range(3)):
        for znaki in itertools.product((1, -1), repeat=3):
            m = np.zeros((3, 3))
            for i, (p, z) in enumerate(zip(perm, znaki)):
                m[i, p] = z
            if np.linalg.det(m) > 0:
                yield m


def obrot_wokol(os, kat_deg):
    a = np.radians(kat_deg)
    c, s = np.cos(a), np.sin(a)
    i, j = [k for k in range(3) if k != os]
    m = np.eye(3)
    m[i, i], m[i, j], m[j, i], m[j, j] = c, -s, s, c
    return m


def os_symetrii(momenty):
    """Indeks osi, wokół której bryła jest (prawie) osiowo symetryczna, albo None."""
    skala = momenty[2]
    if (momenty[2] - momenty[1]) / skala < TOL_SYM:
        return 0
    if (momenty[1] - momenty[0]) / skala < TOL_SYM:
        return 2
    return None


def roznica(wz_k, md_k, m):
    kand = przeksztalc(md_k, m, np.zeros(3))
    nadmiar = abs(kand.cut(wz_k).Volume())
    brak = abs(wz_k.cut(kand).Volume())
    return nadmiar, brak


def porownaj(wz, md):
    (wz_k, mom), (md_k, _) = do_osi_glownych(wz), do_osi_glownych(md)
    os = os_symetrii(mom)
    if os is None:
        kandydaci = list(orientacje())
    else:
        # bryła prawie obrotowa: oś główna jest pewna, kąt wokół niej trzeba znaleźć
        druga = (os + 1) % 3
        odwr = [np.eye(3), obrot_wokol(druga, 180)]
        kandydaci = [obrot_wokol(os, k) @ f for f in odwr for k in range(0, 360, 3)]
    wyniki = [(sum(roznica(wz_k, md_k, m)), i) for i, m in enumerate(kandydaci)]
    _, best = min(wyniki)
    m_best = kandydaci[best]
    if os is not None:
        # doprecyzowanie kąta co 0,1°
        dok = [obrot_wokol(os, d / 10) @ m_best for d in range(-30, 31)]
        m_best = min(dok, key=lambda m: sum(roznica(wz_k, md_k, m)))
    return roznica(wz_k, md_k, m_best)


if __name__ == "__main__":
    wz, md = wczytaj(sys.argv[1]), wczytaj(sys.argv[2])
    print(f"Objętość: wzorzec {wz.Volume():.1f}, model {md.Volume():.1f} mm³")
    nadmiar, brak = porownaj(wz, md)
    print(f"Po wyrównaniu: nadmiar {nadmiar:.1f} mm³, brak {brak:.1f} mm³")
    print("WYNIK:", "ZGODNY" if nadmiar < TOL_OBJ and brak < TOL_OBJ else "NIEZGODNY")
