"""Deterministic CadQuery block library.

Builds 3D solids purely from validated PartSpec JSON operations.
No arbitrary code execution, no reference to ground truth or test fixtures.
"""
from __future__ import annotations

import math

import cadquery as cq

from drawing2cad.schema import (
    Axis,
    CeownikOp,
    CzopKwadratowyOp,
    FazkaOp,
    Operation,
    OtworKwadratowyOp,
    OtworOp,
    PartSpec,
    Plane,
    PlytaZProfiluOp,
    Profile2D,
    ProfileCircle,
    ProfilePolygon,
    ProfileRect,
    ProfileSlot,
    WalecOp,
    WyciecieVOp,
    ZaokraglenieKrawedziOp,
)


class BuildError(RuntimeError):
    """Raised when a CAD operation fails or produces an invalid solid."""


_AXIS_PLANE: dict[Axis, Plane] = {
    "Z": "XY",
    "X": "YZ",
    "Y": "XZ",
}

_PLANE_NORMAL_AXIS: dict[Plane, Axis] = {
    "XY": "Z",
    "XZ": "Y",
    "YZ": "X",
}


def _fillet_edges_along_axis(
    solid: cq.Workplane,
    axis: Axis,
    points: list[tuple[float, float]],
    radius: float,
    tol: float = 0.75,
) -> cq.Workplane:
    """Fillet edges parallel to `axis` whose centers match 2D `points`."""

    def _edge_2d(e: cq.Edge) -> tuple[float, float]:
        c = e.Center()
        if axis == "Z":
            return (c.x, c.y)
        if axis == "Y":
            return (c.x, c.z)
        return (c.y, c.z)

    selected = []
    for edge in solid.edges(f"|{axis}").vals():
        u, v = _edge_2d(edge)
        if any(abs(u - pu) <= tol and abs(v - pv) <= tol for pu, pv in points):
            selected.append(edge)

    if not selected:
        raise BuildError(
            f"Nie znaleziono krawędzi równoległych do osi {axis} w pobliżu punktów {points} (tol={tol})."
        )
    return solid.newObject(selected).fillet(radius)


def _extrude_profile_2d(
    profile: Profile2D,
    plane: Plane,
    depth: float,
    both: bool = False,
) -> cq.Workplane:
    """Create a 3D prism by extruding a single 2D profile primitive."""
    wp = cq.Workplane(plane)
    if isinstance(profile, ProfileCircle):
        body = wp.center(profile.cx, profile.cy).circle(profile.radius).extrude(depth, both=both)
    elif isinstance(profile, ProfileRect):
        body = (
            wp.center(profile.cx, profile.cy)
            .rect(profile.width, profile.height)
            .extrude(depth, both=both)
        )
    elif isinstance(profile, ProfileSlot):
        body = (
            wp.center(profile.cx, profile.cy)
            .slot2D(profile.length, profile.width)
            .extrude(depth, both=both)
        )
    elif isinstance(profile, ProfilePolygon):
        body = wp.polyline(profile.points).close().extrude(depth, both=both)
        if profile.vertex_fillets:
            axis = _PLANE_NORMAL_AXIS[plane]
            # Group fillets by radius for clean, minimal B-Rep operations
            by_radius: dict[float, list[tuple[float, float]]] = {}
            for vf in profile.vertex_fillets:
                by_radius.setdefault(vf.radius, []).append(vf.point)
            for r, pts in by_radius.items():
                body = _fillet_edges_along_axis(body, axis=axis, points=pts, radius=r)
    else:
        raise BuildError(f"Nieobsługiwany typ profilu 2D: {type(profile)}")
    return body


def _apply_plyta_z_profilu(op: PlytaZProfiluOp) -> cq.Workplane:
    depth = op.thickness / 2.0 if op.centered_extrude else op.thickness
    result: cq.Workplane | None = None
    for shape in op.shapes:
        part = _extrude_profile_2d(shape, plane=op.plane, depth=depth, both=op.centered_extrude)
        result = part if result is None else result.union(part)
    assert result is not None
    return result


def _apply_ceownik(op: CeownikOp) -> cq.Workplane:
    if op.outer_width <= 2 * op.wall_thickness:
        raise BuildError("Szerokość zewnętrzna ceownika musi być większa niż 2 * grubość ścianki.")
    if op.height <= op.web_thickness:
        raise BuildError("Wysokość ceownika musi być większa niż grubość środnika.")

    inner_width = op.outer_width - 2.0 * op.wall_thickness
    inner_height = op.height - op.web_thickness
    web_len = op.web_length if op.web_length is not None else op.length

    if op.side_profile is not None:
        # one coordinate convention for both variants: x in [-L/2, L/2], z in [0, height]
        bb = _extrude_profile_2d(op.side_profile, plane="XZ", depth=1.0).val().BoundingBox()
        tol = 1e-6
        if (
            bb.xmin < -op.length / 2.0 - tol
            or bb.xmax > op.length / 2.0 + tol
            or bb.zmin < -tol
            or bb.zmax > op.height + tol
        ):
            raise BuildError(
                f"side_profile ceownika wychodzi poza zakres x [{-op.length / 2:g}, {op.length / 2:g}], "
                f"z [0, {op.height:g}] (profil: x [{bb.xmin:.2f}, {bb.xmax:.2f}], z [{bb.zmin:.2f}, {bb.zmax:.2f}])."
            )

    if op.side_profile is not None and op.web_length is not None and op.web_length < op.length:
        # Two side walls shaped by side_profile + a web of web_length centred at x = 0.
        wall_profile = _extrude_profile_2d(
            op.side_profile, plane="XZ", depth=op.wall_thickness / 2.0, both=True
        )
        bb = wall_profile.val().BoundingBox()
        y_off = (op.outer_width - op.wall_thickness) / 2.0
        left_wall = wall_profile.translate((0.0, -y_off, 0.0))
        right_wall = wall_profile.translate((0.0, y_off, 0.0))

        if op.open_side == "bottom":
            web_z_center = bb.zmax - op.web_thickness / 2.0
        else:
            web_z_center = bb.zmin + op.web_thickness / 2.0

        web = (
            cq.Workplane("XY")
            .box(web_len, op.outer_width, op.web_thickness)
            .translate((0.0, 0.0, web_z_center))
        )
        return left_wall.union(right_wall).union(web)

    # Full-length U-channel: outer box minus inner cavity, optionally intersected with XZ side profile.
    outer = cq.Workplane("XY").box(op.length, op.outer_width, op.height, centered=(True, True, False))
    cavity = cq.Workplane("XY").box(
        op.length, inner_width, inner_height, centered=(True, True, False)
    )
    if op.open_side == "top":
        cavity = cavity.translate((0.0, 0.0, op.web_thickness))
    u_channel = outer.cut(cavity)

    if op.side_profile is not None:
        prism = _extrude_profile_2d(
            op.side_profile, plane="XZ", depth=op.outer_width / 2.0 + 1.0, both=True
        )
        u_channel = u_channel.intersect(prism)

    return u_channel


def _axis_workplane(axis: Axis, offset: float) -> tuple[cq.Workplane, float]:
    """Workplane perpendicular to `axis` at `offset` along +axis, and the extrude sign that
    points towards +axis. (CadQuery's "XZ" plane has its normal along -Y.)"""
    if axis == "Y":
        return cq.Workplane("XZ").workplane(offset=-offset), -1.0
    return cq.Workplane(_AXIS_PLANE[axis]).workplane(offset=offset), 1.0


def _apply_walec(op: WalecOp) -> cq.Workplane:
    wp, sign = _axis_workplane(op.axis, op.offset)
    wp = wp.circle(op.diameter / 2.0)
    if op.centered:
        return wp.extrude(op.length / 2.0, both=True)
    return wp.extrude(sign * op.length)


def _apply_czop_kwadratowy(op: CzopKwadratowyOp) -> cq.Workplane:
    sketch = cq.Sketch().rect(op.size, op.size)
    if op.corner_radius > 0:
        sketch = sketch.vertices().fillet(op.corner_radius)
    wp, sign = _axis_workplane(op.axis, op.offset)
    return wp.placeSketch(sketch).extrude(sign * op.length)


def _through_span(solid: cq.Workplane, plane: Plane) -> float:
    bb = solid.val().BoundingBox()
    axis = _PLANE_NORMAL_AXIS[plane]
    extent = {"X": max(abs(bb.xmin), abs(bb.xmax)), "Y": max(abs(bb.ymin), abs(bb.ymax)), "Z": max(abs(bb.zmin), abs(bb.zmax))}[axis]
    return max(extent * 2.0 + 20.0, 100.0)


def _apply_otwor(solid: cq.Workplane, op: OtworOp) -> cq.Workplane:
    span = _through_span(solid, op.plane) if op.depth is None else None
    # one cut per hole: circles of one sketch that touch or overlap (e.g. the two ends of a
    # slot) make a degenerate face and leave loose material behind
    for pos in op.positions:
        wp = cq.Workplane(op.plane).pushPoints([pos]).circle(op.diameter / 2.0)
        cutter = wp.extrude(span, both=True) if span is not None else wp.extrude(op.depth)
        solid = solid.cut(cutter)
    return solid


def _apply_otwor_kwadratowy(solid: cq.Workplane, op: OtworKwadratowyOp) -> cq.Workplane:
    sketch = cq.Sketch().rect(op.size, op.size)
    if op.corner_radius > 0:
        sketch = sketch.vertices().fillet(op.corner_radius)
    span = _through_span(solid, op.plane) if op.depth is None else op.depth
    for px, py in op.positions:
        wp = cq.Workplane(op.plane).center(px, py).placeSketch(sketch)
        cutter = wp.extrude(span, both=(op.depth is None))
        solid = solid.cut(cutter)
    return solid


def _apply_wyciecie_v(solid: cq.Workplane, op: WyciecieVOp) -> cq.Workplane:
    half_angle_rad = math.radians(op.angle_deg / 2.0)
    half_width = op.depth * math.tan(half_angle_rad)
    tip_z = op.top_z - op.depth if op.direction == "down" else op.top_z + op.depth
    pts = [
        (op.center_x - half_width, op.top_z),
        (op.center_x + half_width, op.top_z),
        (op.center_x, tip_z),
    ]
    span = _through_span(solid, op.plane)
    cutter = cq.Workplane(op.plane).polyline(pts).close().extrude(span, both=True)
    return solid.cut(cutter)


def _apply_zaokraglenie(solid: cq.Workplane, op: ZaokraglenieKrawedziOp) -> cq.Workplane:
    if op.selector == "face_edges":
        if not op.face_selector:
            raise BuildError("selector='face_edges' wymaga pola face_selector (np. '>Z or <Z').")
        return solid.faces(op.face_selector).edges().fillet(op.radius)
    if not op.points:
        raise BuildError("selector='near_points' wymaga niepustej listy points.")
    return _fillet_edges_along_axis(
        solid, axis=op.axis, points=op.points, radius=op.radius, tol=op.tolerance
    )


def _apply_fazka(solid: cq.Workplane, op: FazkaOp) -> cq.Workplane:
    d1 = op.size
    d2 = op.size * math.tan(math.radians(op.angle_deg))  # 'c x a': other leg = c * tan(a)
    for sel in op.face_selectors:
        if abs(op.angle_deg - 45.0) < 1e-6:
            solid = solid.faces(sel).chamfer(d1)
        else:
            solid = solid.faces(sel).chamfer(d1, d2)
    return solid


def execute_operation(solid: cq.Workplane | None, op: Operation) -> cq.Workplane:
    """Execute a single CAD block operation on the current solid."""
    try:
        if isinstance(op, PlytaZProfiluOp):
            part = _apply_plyta_z_profilu(op)
            return part if solid is None else solid.union(part)
        if isinstance(op, CeownikOp):
            part = _apply_ceownik(op)
            return part if solid is None else solid.union(part)
        if isinstance(op, WalecOp):
            part = _apply_walec(op)
            return part if solid is None else solid.union(part)
        if isinstance(op, CzopKwadratowyOp):
            part = _apply_czop_kwadratowy(op)
            return part if solid is None else solid.union(part)

        if solid is None:
            raise BuildError(f"Operacja modyfikująca '{op.op}' wymaga wcześniejszej bryły bazowej.")

        if isinstance(op, OtworOp):
            return _apply_otwor(solid, op)
        if isinstance(op, OtworKwadratowyOp):
            return _apply_otwor_kwadratowy(solid, op)
        if isinstance(op, WyciecieVOp):
            return _apply_wyciecie_v(solid, op)
        if isinstance(op, ZaokraglenieKrawedziOp):
            return _apply_zaokraglenie(solid, op)
        if isinstance(op, FazkaOp):
            return _apply_fazka(solid, op)

        raise BuildError(f"Nieznana operacja: {op}")
    except BuildError:
        raise
    except Exception as exc:
        raise BuildError(f"Błąd geometrii w operacji '{op.op}': {exc}") from exc


def build_part(spec: PartSpec) -> cq.Workplane:
    """Deterministically build a CadQuery solid from a validated PartSpec."""
    solid: cq.Workplane | None = None
    for idx, op in enumerate(spec.operations):
        try:
            solid = execute_operation(solid, op)
        except BuildError as exc:
            raise BuildError(f"Krok {idx + 1} ({op.op}): {exc}") from exc

    if solid is None:
        raise BuildError("Brak operacji budujących bryłę.")

    val = solid.val()
    if not val.isValid():
        raise BuildError("Zbudowana bryła B-Rep jest niepoprawna (isValid() == False).")
    if val.Volume() <= 0:
        raise BuildError(f"Zbudowana bryła ma niepoprawną objętość: {val.Volume()}.")
    return solid
