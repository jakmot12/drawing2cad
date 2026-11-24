"""Modele wzorcowe części podnośnika nożycowego (rys. 01.01.00) w CadQuery.

Każda funkcja zwraca bryłę zbudowaną wyłącznie z wymiarów rysunku.
Założenia przy niejednoznacznościach są opisane w ground_truth.json.
Gwinty modelowane jako walce o średnicy nominalnej (bez geometrii gwintu).
"""
import cadquery as cq


def _fillet_edges_y(solid, points, r, tol=0.5):
    """Zaokrągla krawędzie równoległe do osi Y leżące w punktach (x, z)."""
    def near(e):
        c = e.Center()
        return any(abs(c.x - x) < tol and abs(c.z - z) < tol for x, z in points)
    edges = [e for e in solid.edges("|Y").vals() if near(e)]
    return solid.newObject(edges).fillet(r)


def korona():
    """01.01.01 Korona: ceownik odwrócony 50 x 36 x 28, blacha 3."""
    # Kształt U: blok minus wnętrze (otwarte od dołu)
    u = (cq.Workplane("XY").box(50, 36, 28, centered=(True, True, False))
         .cut(cq.Workplane("XY").box(50, 30, 25, centered=(True, True, False))))
    # Obrys z widoku głównego: dolne narożniki R10, wycięcie V 90° głębokości 7,5
    profil = (cq.Workplane("XZ").center(0, 14).rect(50, 28).extrude(18, both=True))
    profil = _fillet_edges_y(profil, [(-25, 0), (25, 0)], 10)
    v = (cq.Workplane("XZ").polyline([(-7.5, 28), (7.5, 28), (0, 20.5)]).close()
         .extrude(20, both=True))
    bryla = u.intersect(profil).cut(v)
    # 2 x Ø10, położenie przyjęte: współśrodkowo z R10 (x = ±15, z = 10)
    otwory = (cq.Workplane("XZ").pushPoints([(-15, 10), (15, 10)])
              .circle(5).extrude(20, both=True))
    return bryla.cut(otwory)


def podstawa():
    """01.01.02 Podstawa: ceownik 100 x 36 x 28, blacha 3, ścięcia 45°."""
    u = (cq.Workplane("XY").box(100, 36, 28, centered=(True, True, False))
         .cut(cq.Workplane("XY").box(100, 30, 25, centered=(True, True, False))
              .translate((0, 0, 3))))
    # wysokość 28 = 6 + (100 - 56) / 2 przy kącie 45°
    pts = [(-50, 0), (50, 0), (50, 6), (28, 28), (-28, 28), (-50, 6)]
    profil = cq.Workplane("XZ").polyline(pts).close().extrude(18, both=True)
    profil = _fillet_edges_y(profil, [(50, 6), (28, 28), (-28, 28), (-50, 6)], 10)
    bryla = u.intersect(profil)
    otwory = (cq.Workplane("XZ").pushPoints([(-12.5, 16), (12.5, 16)])
              .circle(5).extrude(20, both=True))
    return bryla.cut(otwory)


def dzwignia():
    """01.01.03 Dźwignia: płaskownik 6 mm, główka R10, ramię 15 x 100."""
    glowka = cq.Workplane("XY").circle(10).extrude(6)
    ramie = cq.Workplane("XY").center(50, 0).rect(100, 15).extrude(6)
    b = glowka.union(ramie)
    # R6 na końcu ramienia
    b = b.edges("|Z").edges(cq.selectors.BoxSelector((99, -8, -1), (101, 8, 7))).fillet(6)
    # R6 przejście główka-ramię (krawędzie wklęsłe)
    x0 = (10**2 - 7.5**2) ** 0.5
    b = b.edges("|Z").edges(cq.selectors.BoxSelector((x0 - 0.5, -8, -1), (x0 + 0.5, 8, 7))).fillet(6)
    # R1 na krawędziach zewnętrznych (widok z boku)
    b = b.faces(">Z or <Z").edges().fillet(1)
    # otwór kwadratowy □7 z narożami R1
    kw = cq.Workplane("XY").placeSketch(cq.Sketch().rect(7, 7).vertices().fillet(1)).extrude(6)
    return b.cut(kw)


def oska():
    """01.01.04 Ośka: korpus Ø16 x 21, czopy M10 x 15, otwór poprzeczny M10."""
    korpus = cq.Workplane("XY").circle(8).extrude(21).translate((0, 0, -10.5))
    czop_g = cq.Workplane("XY").workplane(offset=10.5).circle(5).extrude(15).faces(">Z").chamfer(1)
    czop_d = cq.Workplane("XY").workplane(offset=-10.5).circle(5).extrude(-15).faces("<Z").chamfer(1)
    b = korpus.union(czop_g).union(czop_d)
    # otwór M10 na wylot (średnica nominalna)
    return b.cut(cq.Workplane("XZ").circle(5).extrude(10, both=True))


def sruba():
    """01.01.05 Śruba: Ø10 x 310, M10 lewy 150 + M10 150, czop □7 x 10."""
    kw = cq.Workplane("YZ").placeSketch(cq.Sketch().rect(7, 7).vertices().fillet(1)).extrude(10)
    trzpien = cq.Workplane("YZ").workplane(offset=10).circle(5).extrude(300).faces(">X").chamfer(1)
    return kw.union(trzpien)


def ramie(rozstaw=125.0):
    """01.01.06 Ramię: ceownik 30 x 24, blacha 3, otwory Ø10 co 125, końce R12."""
    def scianka(y):
        s = (cq.Workplane("XZ").slot2D(rozstaw + 24, 24).extrude(1.5, both=True)
             .translate((0, y, 0)))
        return s
    b = scianka(13.5).union(scianka(-13.5))
    # środnik: 30 x 3, długość przyjęta = rozstaw otworów (patrz ground_truth)
    srodnik = cq.Workplane("XY").box(rozstaw, 30, 3).translate((0, 0, 10.5))
    b = b.union(srodnik)
    otwory = (cq.Workplane("XZ").pushPoints([(-rozstaw / 2, 0), (rozstaw / 2, 0)])
              .circle(5).extrude(20, both=True))
    return b.cut(otwory)


CZESCI = {
    "01.01.01": korona,
    "01.01.02": podstawa,
    "01.01.03": dzwignia,
    "01.01.04": oska,
    "01.01.05": sruba,
    "01.01.06": ramie,
}
