"""B-Rep geometric measurement & dimension-by-dimension verifier (Rule 4).

Measures real features directly from the CadQuery/OCP solid (bounding box,
cylindrical holes & shafts, fillet radii, chamfers, opposing planar thicknesses,
angled planes / V-notches) and checks off EVERY extracted PDF dimension.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cadquery as cq
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.GeomAbs import (
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Plane,
    GeomAbs_Torus,
)
from OCP.gp import gp_Pnt
from OCP.TopAbs import TopAbs_FACE, TopAbs_IN
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from drawing2cad.drawing_compare import ISO_PITCH_MM
from drawing2cad.drawing_compare import TOL_MM as TOL_VIEW_MM
from drawing2cad.schema import (
    Ambiguity,
    CheckStatus,
    DimensionBinding,
    DimensionCheck,
    ExtractedDimension,
    PartSpec,
    VerificationReport,
    ViewCheck,
)

# The builder creates solids from the exact PDF numbers, so any deviation above numerical
# noise is a wrong parameter, not an OCCT rounding effect.
TOL_LEN = 0.005  # mm
TOL_ANGLE = 0.1  # degrees


@dataclass
class MeasuredCylinder:
    radius: float
    axis_origin: tuple[float, float, float]
    axis_dir: tuple[float, float, float]
    is_concave: bool  # True = hole / inner arc, False = shaft / outer arc
    angular_span_deg: float
    axial_min: float
    axial_max: float
    intervals: list[tuple[float, float]] = field(default_factory=list)  # covered axial ranges

    @property
    def diameter(self) -> float:
        return 2.0 * self.radius

    @property
    def axial_length(self) -> float:
        """Length actually covered by the cylinder (separate coaxial pins are not bridged)."""
        if not self.intervals:
            return self.axial_max - self.axial_min
        total, cur = 0.0, None
        for a, b in sorted(self.intervals):
            if cur is None or a > cur[1] + 1e-6:
                if cur:
                    total += cur[1] - cur[0]
                cur = [a, b]
            else:
                cur[1] = max(cur[1], b)
        return total + (cur[1] - cur[0] if cur else 0.0)

    @property
    def is_full_circle(self) -> bool:
        return self.angular_span_deg >= 340.0


@dataclass
class MeasuredPlane:
    normal: tuple[float, float, float]  # outward unit normal
    offset: float  # dot(point, normal)
    center: tuple[float, float, float]
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    face: object = None  # TopoDS_Face, for points inside the face


@dataclass
class MeasuredChamfer:
    size: float
    angle_deg: float
    axis_origin: tuple[float, float, float] | None = None  # cone axis (None for planar chamfers)
    axis_dir: tuple[float, float, float] | None = None
    alt: bool = False  # the same face read with the legs swapped (matching only, never counted)


@dataclass
class SolidMeasurements:
    bbox_dims: tuple[float, float, float]  # (xlen, ylen, zlen)
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    volume: float
    is_valid: bool
    holes: list[MeasuredCylinder] = field(default_factory=list)
    shafts: list[MeasuredCylinder] = field(default_factory=list)
    chamfers: list[MeasuredChamfer] = field(default_factory=list)
    material_thicknesses: list[float] = field(default_factory=list)
    cavity_widths: list[float] = field(default_factory=list)
    plane_pair_distances: list[float] = field(default_factory=list)
    v_notches: list[tuple[float, float]] = field(default_factory=list)  # (angle_deg, depth)
    bevel_angles: list[float] = field(default_factory=list)
    virtual_edge_lengths: list[float] = field(default_factory=list)
    feature_center_edge_dists: list[float] = field(default_factory=list)
    thickness_features: list[tuple[str, float]] = field(default_factory=list)  # (normal axis, t)
    arcs: list[MeasuredCylinder] = field(default_factory=list)  # partial cylinders (fillets, ends)
    torus_radii: list[float] = field(default_factory=list)
    cavity_features: list[tuple[str, float]] = field(default_factory=list)  # (normal axis, width)
    blind_holes: list[MeasuredCylinder] = field(default_factory=list)  # hole ends inside material
    partial_holes: list[MeasuredCylinder] = field(default_factory=list)  # other walls on the axis not drilled
    n_solids: int = 1
    n_shells: int = 1
    planes: list = field(default_factory=list)  # (outward normal, centre) of every planar face

    def shoulder_positions(self, axis: tuple[float, float, float]) -> list[float]:
        """Positions along `axis` of planar faces lying across it (shoulders, end faces)."""
        return [_dot(c, axis) for n, c in self.planes if abs(_dot(n, axis)) > 0.999]


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(v: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(v, v))


def _canonical_dir(d: tuple[float, float, float]) -> tuple[float, float, float]:
    """Normalize direction with deterministic sign."""
    n = _norm(d) or 1.0
    x, y, z = d[0] / n, d[1] / n, d[2] / n
    for c in (x, y, z):
        if abs(c) > 1e-6:
            return (x, y, z) if c > 0 else (-x, -y, -z)
    return (x, y, z)


def _point_to_line_dist(
    p: tuple[float, float, float],
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
) -> float:
    diff = _sub(p, origin)
    proj = _dot(diff, direction)
    perp = (
        diff[0] - proj * direction[0],
        diff[1] - proj * direction[1],
        diff[2] - proj * direction[2],
    )
    return _norm(perp)


def _is_point_inside(solid_shape, pt: tuple[float, float, float]) -> bool:
    clf = BRepClass3d_SolidClassifier(solid_shape, gp_Pnt(*pt), 1e-4)
    return clf.State() == TopAbs_IN


def _merge_angle_intervals(intervals: list[tuple[float, float]]) -> float:
    """Return total unique angular coverage in degrees for intervals in [0, 360]."""
    normed: list[tuple[float, float]] = []
    for a0, a1 in intervals:
        span = a1 - a0
        if span >= 360.0 - 1e-3:
            return 360.0
        s = a0 % 360.0
        e = s + span
        if e <= 360.0:
            normed.append((s, e))
        else:
            normed.append((s, 360.0))
            normed.append((0.0, e - 360.0))
    if not normed:
        return 0.0
    normed.sort()
    merged = [normed[0]]
    for s, e in normed[1:]:
        ps, pe = merged[-1]
        if s <= pe + 1e-2:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return sum(e - s for s, e in merged)


def measure_solid(workplane: cq.Workplane) -> SolidMeasurements:
    """Measure geometric features directly from the CadQuery/OCP B-Rep solid."""
    solid = workplane.val()
    shape = solid.wrapped
    bb = solid.BoundingBox()

    m = SolidMeasurements(
        bbox_dims=(round(bb.xlen, 4), round(bb.ylen, 4), round(bb.zlen, 4)),
        bbox_min=(round(bb.xmin, 4), round(bb.ymin, 4), round(bb.zmin, 4)),
        bbox_max=(round(bb.xmax, 4), round(bb.ymax, 4), round(bb.zmax, 4)),
        volume=float(solid.Volume()),
        is_valid=bool(solid.isValid()),
    )

    # Raw cylindrical face groups keyed by coaxial axis + radius + concavity
    cyl_groups: list[dict] = []
    planes: list[MeasuredPlane] = []
    torus_radii: list[float] = []

    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        surf = BRepAdaptor_Surface(face, True)
        stype = surf.GetType()
        u0, u1 = surf.FirstUParameter(), surf.LastUParameter()
        v0, v1 = surf.FirstVParameter(), surf.LastVParameter()
        um, vm = (u0 + u1) / 2.0, (v0 + v1) / 2.0
        mid_pt = surf.Value(um, vm)
        mid = (mid_pt.X(), mid_pt.Y(), mid_pt.Z())

        if stype == GeomAbs_Cylinder:
            cyl = surf.Cylinder()
            r = float(cyl.Radius())
            ax = cyl.Axis()
            loc = ax.Location()
            d = ax.Direction()
            origin = (loc.X(), loc.Y(), loc.Z())
            direction = _canonical_dir((d.X(), d.Y(), d.Z()))

            # Project mid onto axis to get radial vector pointing outward from axis
            diff = _sub(mid, origin)
            t_mid = _dot(diff, direction)
            axis_pt = (
                origin[0] + t_mid * direction[0],
                origin[1] + t_mid * direction[1],
                origin[2] + t_mid * direction[2],
            )
            rad = _sub(mid, axis_pt)
            rn = _norm(rad) or 1.0
            rad_unit = (rad[0] / rn, rad[1] / rn, rad[2] / rn)

            # Probe slightly inward (toward cylinder axis): if inside solid -> convex (shaft); else concave (hole)
            eps = min(0.05, r * 0.1)
            inward_pt = (
                mid[0] - eps * rad_unit[0],
                mid[1] - eps * rad_unit[1],
                mid[2] - eps * rad_unit[2],
            )
            is_concave = not _is_point_inside(shape, inward_pt)

            # Axial range along canonical direction
            p0 = surf.Value(um, v0)
            p1 = surf.Value(um, v1)
            t0 = _dot((p0.X(), p0.Y(), p0.Z()), direction)
            t1 = _dot((p1.X(), p1.Y(), p1.Z()), direction)
            tmin, tmax = min(t0, t1), max(t0, t1)

            # Angular interval around axis (in degrees)
            a0, a1 = math.degrees(u0), math.degrees(u1)
            if a1 < a0:
                a0, a1 = a1, a0

            # Merge into coaxial group with same radius and concavity
            matched = None
            for g in cyl_groups:
                if (
                    g["is_concave"] == is_concave
                    and abs(g["radius"] - r) < 0.05
                    and abs(_dot(g["dir"], direction)) > 0.999
                    and _point_to_line_dist(origin, g["origin"], g["dir"]) < 0.05
                ):
                    matched = g
                    break
            if matched is None:
                cyl_groups.append(
                    {
                        "radius": r,
                        "origin": origin,
                        "dir": direction,
                        "is_concave": is_concave,
                        "angles": [(a0, a1)],
                        "tmin": tmin,
                        "tmax": tmax,
                        "ivs": [(tmin, tmax)],
                    }
                )
            else:
                matched["angles"].append((a0, a1))
                matched["tmin"] = min(matched["tmin"], tmin)
                matched["tmax"] = max(matched["tmax"], tmax)
                matched["ivs"].append((tmin, tmax))

        elif stype == GeomAbs_Torus:
            tor = surf.Torus()
            torus_radii.append(round(float(tor.MinorRadius()), 4))

        elif stype == GeomAbs_Cone:
            cone = surf.Cone()
            semi_angle = abs(math.degrees(float(cone.SemiAngle())))
            ax = cone.Axis()
            d = ax.Direction()
            direction = _canonical_dir((d.X(), d.Y(), d.Z()))
            p0 = surf.Value(um, v0)
            p1 = surf.Value(um, v1)
            t0 = _dot((p0.X(), p0.Y(), p0.Z()), direction)
            t1 = _dot((p1.X(), p1.Y(), p1.Z()), direction)
            axial_len = abs(t1 - t0)
            if axial_len > 0.05:
                loc = ax.Location()
                org = (loc.X(), loc.Y(), loc.Z())
                # "c x a": leg c along the axis, angle a between chamfer and axis
                m.chamfers.append(MeasuredChamfer(size=round(axial_len, 4), angle_deg=round(semi_angle, 2),
                                                  axis_origin=org, axis_dir=direction))
                radial = axial_len * math.tan(math.radians(semi_angle))
                m.chamfers.append(MeasuredChamfer(size=round(radial, 4), angle_deg=round(90.0 - semi_angle, 2),
                                                  axis_origin=org, axis_dir=direction, alt=True))

        elif stype == GeomAbs_Plane:
            pln = surf.Plane()
            ax = pln.Axis()
            d = ax.Direction()
            n_vec = (d.X(), d.Y(), d.Z())
            # Determine true outward normal by probing a point slightly along +n_vec
            eps = 0.05
            probe_plus = (mid[0] + eps * n_vec[0], mid[1] + eps * n_vec[1], mid[2] + eps * n_vec[2])
            if _is_point_inside(shape, probe_plus):
                n_vec = (-n_vec[0], -n_vec[1], -n_vec[2])
            offset = _dot(mid, n_vec)
            cq_face = cq.Face(face)
            fbb = cq_face.BoundingBox()
            # planar chamfer / bevel: normal in a coordinate plane, tilted against both axes
            comps = [abs(n_vec[0]), abs(n_vec[1]), abs(n_vec[2])]
            tilted = [k for k in range(3) if comps[k] > 0.05]
            if len(tilted) == 2:
                ext = (fbb.xlen, fbb.ylen, fbb.zlen)
                i, j = tilted
                di, dj = ext[i], ext[j]
                if di > 0.05 and dj > 0.05:
                    m.chamfers.append(MeasuredChamfer(size=round(di, 4), angle_deg=round(math.degrees(math.atan2(dj, di)), 2)))
                    m.chamfers.append(MeasuredChamfer(size=round(dj, 4), angle_deg=round(math.degrees(math.atan2(di, dj)), 2), alt=True))
            planes.append(
                MeasuredPlane(
                    normal=n_vec,
                    offset=offset,
                    center=mid,
                    bounds_min=(fbb.xmin, fbb.ymin, fbb.zmin),
                    bounds_max=(fbb.xmax, fbb.ymax, fbb.zmax),
                    face=face,
                )
            )

        exp.Next()

    # Classify coaxial cylinder groups into holes, shafts, and fillets
    for g in cyl_groups:
        span_deg = _merge_angle_intervals(g["angles"])
        mc = MeasuredCylinder(
            radius=round(g["radius"], 4),
            axis_origin=g["origin"],
            axis_dir=g["dir"],
            is_concave=g["is_concave"],
            angular_span_deg=round(span_deg, 2),
            axial_min=round(g["tmin"], 4),
            axial_max=round(g["tmax"], 4),
            intervals=[(round(a, 4), round(b, 4)) for a, b in g["ivs"]],
        )
        if mc.is_full_circle:
            if mc.is_concave:
                m.holes.append(mc)
            else:
                m.shafts.append(mc)
        else:
            m.arcs.append(mc)
            # Record center-to-bbox-face distances for large cylindrical arcs (e.g. lever head R10)
            if mc.angular_span_deg >= 120.0:
                ox, oy, oz = mc.axis_origin
                dx, dy, dz = mc.axis_dir
                if abs(dx) < 0.01:
                    m.feature_center_edge_dists.extend(
                        [round(abs(ox - m.bbox_min[0]), 4), round(abs(m.bbox_max[0] - ox), 4)]
                    )
                if abs(dy) < 0.01:
                    m.feature_center_edge_dists.extend(
                        [round(abs(oy - m.bbox_min[1]), 4), round(abs(m.bbox_max[1] - oy), 4)]
                    )
                if abs(dz) < 0.01:
                    m.feature_center_edge_dists.extend(
                        [round(abs(oz - m.bbox_min[2]), 4), round(abs(m.bbox_max[2] - oz), 4)]
                    )

    m.torus_radii = list(torus_radii)

    m.planes = [(pl.normal, pl.center) for pl in planes]

    # Analyze planar faces: thicknesses, cavities, segment lengths, angled bevels, V-notches
    n_planes = len(planes)
    for i in range(n_planes):
        pi = planes[i]
        for j in range(i + 1, n_planes):
            pj = planes[j]
            ndot = _dot(pi.normal, pj.normal)

            # Opposing parallel planes (outward normals opposite)
            if ndot < -0.999:
                # Signed distance along pi.normal from pj to pi:
                # If positive, pi and pj face AWAY from each other (material between them);
                # If negative, pi and pj face TOWARD each other (cavity/slot/hole between them).
                signed_dist = pi.offset + pj.offset
                dist = abs(signed_dist)
                if dist < 0.05:
                    continue
                m.plane_pair_distances.append(round(dist, 4))
                # Probe midpoint between face centers to check whether it's material or cavity
                if signed_dist > 0:
                    # Also check local material thickness by probing from pi center toward pj
                    probe = (
                        pi.center[0] - (dist / 2.0) * pi.normal[0],
                        pi.center[1] - (dist / 2.0) * pi.normal[1],
                        pi.center[2] - (dist / 2.0) * pi.normal[2],
                    )
                    if _is_point_inside(shape, probe) or _material_between(shape, pi, pj, dist):
                        m.material_thicknesses.append(round(dist, 4))
                        m.thickness_features.append((_axis_label(pi.normal), round(dist, 4)))
                else:
                    probe = (
                        pi.center[0] + (dist / 2.0) * pi.normal[0],
                        pi.center[1] + (dist / 2.0) * pi.normal[1],
                        pi.center[2] + (dist / 2.0) * pi.normal[2],
                    )
                    if not _is_point_inside(shape, probe):
                        m.cavity_widths.append(round(dist, 4))
                        m.cavity_features.append((_axis_label(pi.normal), round(dist, 4)))
                        # Cavity midplane coordinate along axis -> distance to bbox faces
                        mid_coord = (pi.offset - pj.offset) / 2.0
                        nx, ny, nz = pi.normal
                        if abs(nx) > 0.999:
                            cx = mid_coord * nx
                            m.feature_center_edge_dists.extend(
                                [round(abs(cx - m.bbox_min[0]), 4), round(abs(m.bbox_max[0] - cx), 4)]
                            )
                        elif abs(ny) > 0.999:
                            cy = mid_coord * ny
                            m.feature_center_edge_dists.extend(
                                [round(abs(cy - m.bbox_min[1]), 4), round(abs(m.bbox_max[1] - cy), 4)]
                            )
                        elif abs(nz) > 0.999:
                            cz = mid_coord * nz
                            m.feature_center_edge_dists.extend(
                                [round(abs(cz - m.bbox_min[2]), 4), round(abs(m.bbox_max[2] - cz), 4)]
                            )

            # Same-direction parallel planes (steps / ledges, e.g. 15, 21, 150)
            elif ndot > 0.999:
                step_dist = abs(pi.offset - pj.offset)
                if step_dist > 0.05:
                    m.plane_pair_distances.append(round(step_dist, 4))

            # Angled planes (bevels, chamfers, V-notches)
            elif abs(ndot) < 0.995:
                # Angle between planes (0..180)
                ang = math.degrees(math.acos(max(-1.0, min(1.0, ndot))))
                # Check if one plane is axis-aligned and the other is tilted -> bevel/chamfer angle
                pi_axis = max(abs(c) for c in pi.normal) > 0.999
                pj_axis = max(abs(c) for c in pj.normal) > 0.999
                if pi_axis != pj_axis:
                    tilt = min(ang, 180.0 - ang)
                    if 5.0 < tilt < 85.0:
                        m.bevel_angles.append(round(tilt, 2))
                elif not pi_axis and not pj_axis:
                    # Two tilted planes: could be symmetric V-notch or symmetric trapezoid bevels
                    # Check if their normals are symmetric about an axis plane
                    line_dir = _cross(pi.normal, pj.normal)
                    if _norm(line_dir) > 1e-4:
                        # Interior angle between two faces = 180 - angle(outward normals)
                        interior_ang = 180.0 - ang
                        # Check if the planes meet at a concave valley (V-notch)
                        z_valley = min(pi.bounds_min[2], pj.bounds_min[2])
                        z_top = max(pi.bounds_max[2], pj.bounds_max[2])
                        # Check midpoint slightly above the shared valley edge
                        mid_xy = (
                            (pi.center[0] + pj.center[0]) / 2.0,
                            (pi.center[1] + pj.center[1]) / 2.0,
                            (pi.center[2] + pj.center[2]) / 2.0,
                        )
                        if not _is_point_inside(shape, mid_xy) and z_top - z_valley > 0.5:
                            m.v_notches.append((round(interior_ang, 2), round(z_top - z_valley, 4)))

    # Virtual intersection lengths: edges dimensioned to the intersection of the lines
    # before rounding (e.g. a bevelled top edge measured to the virtual sharp corners):
    # Intersect tilted bevel planes with extreme bounding planes.
    tilted_planes = [p for p in planes if max(abs(c) for c in p.normal) < 0.99]
    if len(tilted_planes) >= 2:
        for p in tilted_planes:
            nx, ny, nz = p.normal
            if abs(ny) < 1e-3 and abs(nx) > 0.1 and abs(nz) > 0.1:
                # Line in XZ: nx * x + nz * z = p.offset
                # Intersection with z = zmax: x = (offset - nz * zmax) / nx
                x_at_top = (p.offset - nz * bb.zmax) / nx
                x_at_bot = (p.offset - nz * bb.zmin) / nx
                # Intersection with x = xmax or xmin: z = (offset - nx * x_ext) / nz
                x_ext = bb.xmax if nx > 0 else bb.xmin
                z_at_side = (p.offset - nx * x_ext) / nz
                if bb.zmin < z_at_side < bb.zmax:
                    m.virtual_edge_lengths.append(round(z_at_side - bb.zmin, 4))
                    m.virtual_edge_lengths.append(round(bb.zmax - z_at_side, 4))
                # Store x_at_top for symmetric pair width
                for q in tilted_planes:
                    if q is p:
                        continue
                    qnx, qny, qnz = q.normal
                    if abs(qny) < 1e-3 and abs(qnx) > 0.1 and abs(qnz) > 0.1 and nx * qnx < 0:
                        qx_at_top = (q.offset - qnz * bb.zmax) / qnx
                        m.virtual_edge_lengths.append(round(abs(x_at_top - qx_at_top), 4))
                        qx_at_bot = (q.offset - qnz * bb.zmin) / qnx
                        m.virtual_edge_lengths.append(round(abs(x_at_bot - qx_at_bot), 4))

    for ax, ext in zip("XYZ", m.bbox_dims):
        m.thickness_features.append((ax, ext))

    from OCP.TopAbs import TopAbs_SHELL, TopAbs_SOLID

    def _count(kind) -> int:
        e, n = TopExp_Explorer(shape, kind), 0
        while e.More():
            n += 1
            e.Next()
        return n

    m.n_solids, m.n_shells = _count(TopAbs_SOLID), _count(TopAbs_SHELL)

    # a hole without a depth dimension is a through hole: its whole axis inside the part's
    # bounding box must be free of material (otherwise it stops in a wall: blind hole)
    for h in m.holes:
        base = _dot(h.axis_origin, h.axis_dir)
        ends = [h.axial_min - 0.05, h.axial_max + 0.05]
        probes = [tuple(h.axis_origin[i] + (t - base) * h.axis_dir[i] for i in range(3)) for t in ends]
        if any(_is_point_inside(shape, q) for q in probes) or _ends_on_shoulder(shape, h):
            m.blind_holes.append(h)  # the hole ends inside material: a bottom, or a counterbore's shoulder
        elif _axis_has_material(shape, h.axis_origin, h.axis_dir, m):
            m.partial_holes.append(h)  # through its own wall(s), but another wall on the axis is not drilled
    return m


def _ends_on_shoulder(shape, h: MeasuredCylinder) -> bool:
    """A hole whose end continues in a narrower coaxial hole (counterbore, stepped bore): just
    beyond that end there is a full ring of material near the hole's wall. A hole leaving the
    part through any surface has empty space there on at least one side."""
    d = np.array(h.axis_dir)
    u = np.cross(d, [1.0, 0.0, 0.0] if abs(d[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(d, u)
    o = np.array(h.axis_origin) - np.dot(h.axis_origin, d) * d
    rr = max(h.radius - 0.05, 0.9 * h.radius)
    for t in (h.axial_min - 0.05, h.axial_max + 0.05):
        ring = [o + t * d + rr * (math.cos(a) * u + math.sin(a) * v) for a in np.radians(np.arange(0, 360, 30))]
        if all(_is_point_inside(shape, tuple(float(c) for c in p)) for p in ring):
            return True
    return False


def _face_samples(pl: MeasuredPlane, grid: int = 6) -> list[tuple[float, float, float]]:
    """Points inside a planar face (its parameter centre may lie in a hole)."""
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.gp import gp_Pnt2d

    if pl.face is None:
        return []
    surf = BRepAdaptor_Surface(pl.face, True)
    u0, u1, v0, v1 = surf.FirstUParameter(), surf.LastUParameter(), surf.FirstVParameter(), surf.LastVParameter()
    clf = BRepClass_FaceClassifier()
    out = []
    for i in range(1, grid):
        for j in range(1, grid):
            u, v = u0 + (u1 - u0) * i / grid, v0 + (v1 - v0) * j / grid
            clf.Perform(pl.face, gp_Pnt2d(u, v), 1e-6)
            if clf.State() == TopAbs_IN:
                q = surf.Value(u, v)
                out.append((q.X(), q.Y(), q.Z()))
    return out


def _material_between(shape, pi: MeasuredPlane, pj: MeasuredPlane, dist: float) -> bool:
    """Two opposite faces with material between them somewhere they overlap (not only at the
    centre of pi, which can fall into a hole of the face)."""
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.TopAbs import TopAbs_ON

    if pj.face is None:
        return False
    clf = BRepClass_FaceClassifier()
    n = pi.normal
    for p in _face_samples(pi):
        q = tuple(p[k] - dist * n[k] for k in range(3))
        clf.Perform(pj.face, gp_Pnt(*q), 1e-4)
        if clf.State() not in (TopAbs_IN, TopAbs_ON):
            continue
        if _is_point_inside(shape, tuple(p[k] - 0.5 * dist * n[k] for k in range(3))):
            return True
    return False


def _axis_has_material(shape, origin, direction, m: SolidMeasurements) -> bool:
    """True when the line through `origin` along `direction` meets material inside the bbox."""
    corners = [(x, y, z) for x in (m.bbox_min[0], m.bbox_max[0]) for y in (m.bbox_min[1], m.bbox_max[1])
               for z in (m.bbox_min[2], m.bbox_max[2])]
    ts = [_dot(c, direction) for c in corners]
    t_lo, t_hi = min(ts), max(ts)
    base = _dot(origin, direction)
    steps = max(2, int((t_hi - t_lo) / 0.25))
    for k in range(steps + 1):
        t = t_lo + (t_hi - t_lo) * k / steps - base
        if _is_point_inside(shape, tuple(origin[i] + t * direction[i] for i in range(3))):
            return True
    return False


def _axis_label(n: tuple[float, float, float]) -> str:
    i = max(range(3), key=lambda k: abs(n[k]))
    return "XYZ"[i] if abs(n[i]) > 0.999 else f"({n[0]:.2f},{n[1]:.2f},{n[2]:.2f})"


def _closest_match(candidates: list[float], target: float) -> float | None:
    if not candidates:
        return None
    return min(candidates, key=lambda v: abs(v - target))


def _hole_pair_distances(holes: list[MeasuredCylinder]) -> list[float]:
    dists: list[float] = []
    for i in range(len(holes)):
        for j in range(i + 1, len(holes)):
            if abs(_dot(holes[i].axis_dir, holes[j].axis_dir)) > 0.999:
                d = _point_to_line_dist(
                    holes[j].axis_origin, holes[i].axis_origin, holes[i].axis_dir
                )
                if d > 0.05:
                    dists.append(round(d, 4))
    return dists


def _pitch_circle_groups(holes: list[MeasuredCylinder]) -> list[tuple[list[MeasuredCylinder], float]]:
    """Groups of three or more equal parallel holes whose axes lie on one circle: (holes, radius)."""
    groups: list[list[MeasuredCylinder]] = []
    for h in holes:
        for g in groups:
            if abs(g[0].diameter - h.diameter) <= TOL_LEN and abs(_dot(g[0].axis_dir, h.axis_dir)) > 0.999:
                g.append(h)
                break
        else:
            groups.append([h])
    out = []
    for g in groups:
        if len(g) < 3:
            continue
        d = np.array(g[0].axis_dir)
        u = np.cross(d, [1.0, 0.0, 0.0] if abs(d[0]) < 0.9 else [0.0, 1.0, 0.0])
        u /= np.linalg.norm(u)
        v = np.cross(d, u)
        pts = np.array([[np.dot(h.axis_origin, u), np.dot(h.axis_origin, v)] for h in g])
        A = np.column_stack([2 * pts[:, 0], 2 * pts[:, 1], np.ones(len(pts))])
        (cx, cy, c0), *_ = np.linalg.lstsq(A, (pts**2).sum(axis=1), rcond=None)
        r = math.sqrt(max(c0 + cx * cx + cy * cy, 0.0))
        if r > TOL_LEN and np.abs(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r).max() <= TOL_LEN:
            out.append((g, r))
    return out


def _pitch_circle_diameters(holes: list[MeasuredCylinder]) -> list[float]:
    """Diameters of pitch (bolt) circles: the distance of two holes, or the circle through
    the axes of three or more equal parallel holes when they all lie on one circle."""
    return list(_hole_pair_distances(holes)) + [round(2 * r, 4) for _, r in _pitch_circle_groups(holes)]


def _hole_edge_distances(holes: list[MeasuredCylinder], m: SolidMeasurements) -> list[float]:
    """Perpendicular distances from each hole axis to each bounding-box plane and to every
    planar face parallel to the axis (e.g. a shoulder the hole is dimensioned from)."""
    dists: list[float] = []
    for h in holes:
        for n, c in m.planes:
            if abs(_dot(n, h.axis_dir)) < 1e-3:
                dists.append(abs(_dot(n, _sub(c, h.axis_origin))))
        ox, oy, oz = h.axis_origin
        dx, dy, dz = h.axis_dir
        if abs(dx) < 0.01:
            dists.extend([abs(ox - m.bbox_min[0]), abs(m.bbox_max[0] - ox)])
        if abs(dy) < 0.01:
            dists.extend([abs(oy - m.bbox_min[1]), abs(m.bbox_max[1] - oy)])
        if abs(dz) < 0.01:
            dists.extend([abs(oz - m.bbox_min[2]), abs(m.bbox_max[2] - oz)])
    return [round(d, 4) for d in dists]


ALLOWED_TARGETS: dict[str, set[str]] = {
    "dlugosc": {"gabaryt", "dlugosc_odcinka", "otwor_rozstaw", "otwor_wysokosc", "grubosc", "glebokosc_wyciecia",
                "glebokosc_otworu"},
    "srednica": {"otwor_srednica", "walec_srednica", "otwor_rozstaw"},  # Ø of a pitch circle
    "promien": {"promien"},
    "kat": {"kat_wyciecia", "kat_sciecia"},
    "kwadrat": {"kwadrat"},
    "gwint": {"gwint_zewnetrzny", "gwint_wewnetrzny"},
    "fazka": {"fazka"},
}


def allowed_targets(ext: ExtractedDimension) -> set[str]:
    """Model features a drawing dimension may be checked against. When a symbol drawn as curves
    before the value could not be read, the dimension may also be a diameter."""
    allowed = set(ALLOWED_TARGETS.get(ext.dim_type, set()))
    if ext.type_uncertain and ext.dim_type == "dlugosc":
        allowed |= ALLOWED_TARGETS["srednica"] | ALLOWED_TARGETS["kwadrat"]
    return allowed


def _segment_count(c: MeasuredCylinder) -> int:
    """Number of separate axial segments of a cylinder group (e.g. two pins on one axis)."""
    if not c.intervals:
        return 1
    n, end = 0, None
    for a, b in sorted(c.intervals):
        if end is None or a > end + 1e-6:
            n += 1
            end = b
        else:
            end = max(end, b)
    return n


def _thread_length_consistent(
    length: float, spec: PartSpec, m: SolidMeasurements, used: set[int]
) -> tuple[bool, str]:
    """A thread section length (e.g. LH 150 + RH 150 on one rod) is not a solid edge.

    Accept it only when an unused declared thread has exactly this length, every declared
    external thread of that diameter has a length, and together they fit on the measured
    coaxial cylinders of that diameter (plus their own end chamfers)."""
    for i, th in enumerate(spec.threads):
        if i in used or th.is_internal or th.length is None or abs(th.length - length) > TOL_LEN:
            continue
        same_d = [t for t in spec.threads if not t.is_internal and abs(t.nominal_diameter - th.nominal_diameter) <= TOL_LEN]
        if any(t.length is None for t in same_d):
            return False, ""
        total = sum(t.length for t in same_d)
        shafts = [sh for sh in m.shafts if abs(sh.diameter - th.nominal_diameter) <= TOL_LEN]
        best_room = 0.0
        for ref in shafts:
            group = [sh for sh in shafts if _coaxial(ref.axis_origin, ref.axis_dir, sh.axis_origin, sh.axis_dir)]
            room = sum(sh.axial_length for sh in group) + sum(
                c.size for c in m.chamfers
                if c.axis_dir is not None and not c.alt and _coaxial(ref.axis_origin, ref.axis_dir, c.axis_origin, c.axis_dir)
            )
            best_room = max(best_room, room)
        if shafts and total <= best_room + TOL_LEN:
            used.add(i)
            return True, (
                f"długość gwintu {th.spec} ({len(same_d)} odcinki, razem {total:g} mm "
                f"na walcu Ø{th.nominal_diameter:g} długości {best_room:.2f} mm)"
            )
    return False, ""


def _check_binding(
    binding: DimensionBinding,
    extracted: ExtractedDimension,
    m: SolidMeasurements,
    spec: PartSpec,
    uncertain_dims: set[str],
    used_threads: set[int] | None = None,
) -> DimensionCheck:
    target = binding.target
    exp = extracted.value  # numbers come from the PDF only (rule 1)
    mult = max(extracted.multiplicity, binding.multiplicity)  # the drawing's 2x cannot be lowered
    is_uncertain = binding.dim_id in uncertain_dims

    def _result(ok: bool, expected_str: str, measured_str: str, msg: str = "") -> DimensionCheck:
        status: CheckStatus = "BŁĄD" if not ok else ("NIEPEWNE" if is_uncertain else "OK")
        return DimensionCheck(
            dim_id=binding.dim_id,
            raw_text=extracted.decoded_text,
            target=target,
            expected=expected_str,
            measured=measured_str,
            status=status,
            message=msg,
        )

    if exp is None:
        return _result(False, "?", "brak", "Brak wartości liczbowej wymiaru.")

    allowed = allowed_targets(extracted)
    if target not in allowed:
        return _result(
            False,
            extracted.decoded_text,
            f"cel '{target}'",
            f"Wymiar typu '{extracted.dim_type}' ({extracted.decoded_text}) nie może być sprawdzany jako "
            f"'{target}'. Dozwolone: {', '.join(sorted(allowed))}.",
        )

    def _near(cands: list[float], tol: float = TOL_LEN):
        best = _closest_match(cands, exp)
        return best, best is not None and abs(best - exp) <= tol

    if target == "gabaryt":
        best, ok = _near(list(m.bbox_dims))
        return _result(
            ok, f"{exp:g} mm", f"{best:.2f} mm" if best is not None else "brak",
            "" if ok else f"Gabaryt niezgodny: zmierzono {[round(x, 2) for x in m.bbox_dims]}, rysunek {exp:g} mm.",
        )

    if target == "dlugosc_odcinka":
        cands = (
            list(m.bbox_dims)
            + list(m.plane_pair_distances)
            + list(m.virtual_edge_lengths)
            + list(m.feature_center_edge_dists)
            + [round(s.axial_length, 4) for s in m.shafts]
            + _hole_edge_distances(m.holes, m)
        )
        best, ok = _near(cands)
        if not ok:
            thread_ok, why = _thread_length_consistent(exp, spec, m, used_threads if used_threads is not None else set())
            if thread_ok:
                return _result(True, f"{exp:g} mm", why)
        msg = ""
        if not ok:
            msg = f"Odcinek {exp:g} mm nie znaleziony w bryle" + (f" (najbliższy: {best:.2f} mm)." if best is not None else ".")
            if spec.threads and any(t.length is None for t in spec.threads if not t.is_internal):
                msg += " Jeśli to długość odcinka gwintu, wpisz ją w polu 'length' odpowiedniego wpisu w 'threads'."
        return _result(ok, f"{exp:g} mm", f"{best:.2f} mm" if best is not None else "brak", msg)

    if target == "otwor_srednica":
        matching = [h for h in m.holes if abs(h.diameter - exp) <= TOL_LEN]
        all_diams = [round(h.diameter, 2) for h in m.holes]
        if len(matching) >= mult:
            return _result(True, f"{mult}xØ{exp:g}", f"{len(matching)}xØ{exp:g}")
        return _result(
            False, f"{mult}xØ{exp:g}", f"otwory: {all_diams}" if all_diams else "brak otworów",
            f"Oczekiwano {mult}x otwór Ø{exp:g}, zmierzono {all_diams or 'brak otworów'}.",
        )

    if target == "glebokosc_otworu":
        best, ok = _near([h.axial_length for h in m.blind_holes])
        return _result(
            ok, f"{exp:g} mm", f"{best:.2f} mm" if best is not None else "brak otworów nieprzelotowych",
            "" if ok else f"Głębokość otworu nieprzelotowego {best} mm != {exp:g} mm.",
        )

    if target == "otwor_rozstaw":
        if extracted.dim_type == "srednica":  # pitch circle of a hole pattern
            best, ok = _near(_pitch_circle_diameters(m.holes))
            return _result(
                ok, f"Ø{exp:g}", f"Ø{best:.2f}" if best is not None else "brak otworów na okręgu",
                "" if ok else f"Otwory nie leżą na okręgu podziałowym Ø{exp:g} (najbliższy: {best}).",
            )
        best, ok = _near(_hole_pair_distances(m.holes))
        return _result(
            ok, f"{exp:g} mm", f"{best:.2f} mm" if best is not None else "brak par otworów",
            "" if ok else f"Rozstaw otworów {best} mm != {exp:g} mm.",
        )

    if target == "otwor_wysokosc":
        best, ok = _near(_hole_edge_distances(m.holes, m))
        return _result(
            ok, f"{exp:g} mm", f"{best:.2f} mm" if best is not None else "brak otworów",
            "" if ok else f"Odległość osi otworu od krawędzi {best} mm != {exp:g} mm.",
        )

    if target == "walec_srednica":
        best, ok = _near([s.diameter for s in m.shafts])
        return _result(
            ok, f"Ø{exp:g}", f"Ø{best:.2f}" if best is not None else "brak walców",
            "" if ok else f"Średnica walca Ø{best} != Ø{exp:g}.",
        )

    if target == "promien":
        cands = [f.radius for f in m.arcs] + list(m.torus_radii) + [s.radius for s in m.shafts]
        best, ok = _near(cands)
        return _result(
            ok, f"R{exp:g}", f"R{best:.2f}" if best is not None else "brak zaokrągleń",
            "" if ok else f"Promień R{exp:g} nie znaleziony w bryle (zmierzone: {sorted(set(round(r, 2) for r in cands))}).",
        )

    if target == "fazka":
        exp_ang = extracted.secondary_value or 45.0
        matching = [c for c in m.chamfers if abs(c.size - exp) <= TOL_LEN and abs(c.angle_deg - exp_ang) <= TOL_ANGLE]
        if matching:
            n_faces = len({id(c) for c in matching if not c.alt}) + sum(1 for c in matching if c.alt and not any(
                not o.alt and o.axis_origin == c.axis_origin and o.axis_dir == c.axis_dir for o in matching))
            return _result(True, f"{exp:g}x{exp_ang:g}°", f"{max(1, n_faces)} szt. {exp:g}x{exp_ang:g}°")
        return _result(
            False, f"{exp:g}x{exp_ang:g}°", str([(c.size, c.angle_deg) for c in m.chamfers]) or "brak fazek",
            f"Fazka {exp:g}x{exp_ang:g}° nie znaleziona.",
        )

    if target == "kat_wyciecia":
        best, ok = _near([ang for ang, _ in m.v_notches], TOL_ANGLE)
        return _result(
            ok, f"{exp:g}°", f"{best:.1f}°" if best is not None else "brak wycięcia V",
            "" if ok else f"Kąt wycięcia V {best}° != {exp:g}°.",
        )

    if target == "glebokosc_wyciecia":
        best, ok = _near([depth for _, depth in m.v_notches])
        return _result(
            ok, f"{exp:g} mm", f"{best:.2f} mm" if best is not None else "brak wycięcia V",
            "" if ok else f"Głębokość wycięcia V {best} mm != {exp:g} mm.",
        )

    if target == "kat_sciecia":
        best, ok = _near(list(m.bevel_angles), TOL_ANGLE)
        return _result(
            ok, f"{exp:g}°", f"{best:.1f}°" if best is not None else "brak ścięć",
            "" if ok else f"Kąt ścięcia {best}° != {exp:g}°.",
        )

    if target == "grubosc":
        cands = [t for _, t in m.thickness_features]
        best, ok = _near(cands)
        return _result(
            ok, f"{exp:g} mm", f"{best:.2f} mm" if best is not None else "brak",
            "" if ok else f"Grubość {exp:g} mm nie znaleziona (zmierzone: {sorted(set(round(x, 2) for x in cands))[:8]}).",
        )

    if target == "kwadrat":
        feats = list(m.cavity_features) + [f for f in m.thickness_features]
        axes = {ax for ax, v in feats if abs(v - exp) <= TOL_LEN}
        ok = len(axes) >= 2  # a square has two pairs of faces at distance `exp` in perpendicular directions
        best = _closest_match([v for _, v in feats], exp)
        return _result(
            ok, f"□{exp:g}", f"□{best:.2f} (kierunki: {', '.join(sorted(axes)) or 'brak'})" if best is not None else "brak",
            "" if ok else f"Przekrój kwadratowy □{exp:g} nie potwierdzony: potrzebne 2 prostopadłe pary ścian, są {len(axes)}.",
        )

    if target in ("gwint_zewnetrzny", "gwint_wewnetrzny"):
        t_spec = binding.thread_spec or extracted.thread_spec or f"M{exp:g}"
        want_left = "LH" in t_spec.upper() or "LEWY" in t_spec.upper()
        want_internal = target == "gwint_wewnetrzny"
        if want_internal:
            geom_cands = [h.diameter for h in m.holes]
            pitch = ISO_PITCH_MM.get(int(round(exp))) if abs(exp - round(exp)) < 1e-6 else None
            core = exp - 1.0825 * pitch if pitch else None
            # tapped hole may be modelled at the nominal or at the minor (tap drill) diameter
            n_geom = sum(
                1 for d in geom_cands
                if abs(d - exp) <= TOL_LEN or (core is not None and core - TOL_LEN <= d <= core + 0.2)
            )
        else:
            geom_cands = [s.diameter for s in m.shafts]
            # coaxial pins of one diameter form one cylinder group: count its separate segments
            n_geom = sum(_segment_count(s) for s in m.shafts if abs(s.diameter - exp) <= TOL_LEN)
        geom_ok = n_geom >= mult
        best_d = _closest_match(geom_cands, exp)
        meta_ok = any(
            th.is_internal == want_internal
            and abs(th.nominal_diameter - exp) <= TOL_LEN
            and th.direction == ("lewy" if want_left else "prawy")
            for th in spec.threads
        )
        msgs = []
        if not geom_ok:
            msgs.append(
                f"Gwint {mult}x{t_spec}: w modelu {n_geom} pasujących walców/otworów "
                f"(zmierzone średnice: {[round(d, 2) for d in geom_cands]})."
            )
        if not meta_ok:
            msgs.append(f"Brak wpisu gwintu {t_spec} w 'threads' (kierunek: {'lewy' if want_left else 'prawy'}).")
        measured = f"Ø{best_d:.2f}" if best_d is not None else "brak"
        return _result(geom_ok and meta_ok, t_spec, measured + (f" + opis {t_spec}" if meta_ok else ""), " ".join(msgs))

    return _result(False, str(exp), "nieobsługiwany", f"Nieznany cel weryfikacji: {target}")


# targets whose drawing labels each need their own feature in the model
_COUNTED_TARGETS = ("promien", "grubosc", "fazka", "otwor_srednica", "gwint_wewnetrzny")


def _feature_count_errors(
    bindings: list[DimensionBinding], ext_by_id: dict[str, ExtractedDimension], m: SolidMeasurements
) -> list[tuple[list[str], str]]:
    """Two drawing labels must not be satisfied by one and the same model feature."""
    groups: dict[tuple[str, float], list[DimensionBinding]] = {}
    for b in bindings:
        ext = ext_by_id.get(b.dim_id)
        if ext is None or ext.value is None or b.target not in _COUNTED_TARGETS:
            continue
        if b.target not in allowed_targets(ext):
            continue
        key = (b.target, round(ext.value, 4), round(ext.secondary_value or 0.0, 2) if b.target == "fazka" else 0.0)
        groups.setdefault(key, []).append(b)

    errors: list[tuple[list[str], str]] = []
    for (target, v, _ang), bs in groups.items():
        need = sum(max(ext_by_id[b.dim_id].multiplicity, b.multiplicity) for b in bs)
        if target == "promien":
            have = (
                sum(1 for f in m.arcs if abs(f.radius - v) <= TOL_LEN)
                + sum(1 for r in m.torus_radii if abs(r - v) <= TOL_LEN)
                + sum(1 for s in m.shafts if abs(s.radius - v) <= TOL_LEN)
            )
            what = f"zaokrągleń R{v:g}"
        elif target == "grubosc":
            have = len({ax for ax, t in m.thickness_features if abs(t - v) <= TOL_LEN})
            what = f"ścianek grubości {v:g} mm (w różnych kierunkach)"
        elif target == "fazka":
            ang = ext_by_id[bs[0].dim_id].secondary_value or 45.0
            # each chamfer face counted once, in whichever leg orientation it matches
            mains = [c for c in m.chamfers if not c.alt]
            alts = [c for c in m.chamfers if c.alt]
            have = sum(
                1 for mc, ac in zip(mains, alts)
                if (abs(mc.size - v) <= TOL_LEN and abs(mc.angle_deg - ang) <= TOL_ANGLE)
                or (abs(ac.size - v) <= TOL_LEN and abs(ac.angle_deg - ang) <= TOL_ANGLE)
            )
            what = f"fazek {v:g} mm"
        elif target == "gwint_wewnetrzny":
            pitch = ISO_PITCH_MM.get(int(round(v))) if abs(v - round(v)) < 1e-6 else None
            core = v - 1.0825 * pitch if pitch else None
            have = sum(1 for h in m.holes if abs(h.diameter - v) <= TOL_LEN
                       or (core is not None and core - TOL_LEN <= h.diameter <= core + 0.2))
            what = f"otworów gwintowanych M{v:g}"
        else:
            have = sum(1 for h in m.holes if abs(h.diameter - v) <= TOL_LEN)
            what = f"otworów Ø{v:g}"
        if have < need:
            ids = [b.dim_id for b in bs]
            errors.append(
                (ids, f"Rysunek wymaga co najmniej {need} {what} ({', '.join(ids)}), w modelu jest {have}.")
            )
    return errors


HOLE_POSITION_TARGETS = ("otwor_rozstaw", "otwor_wysokosc")


def _coaxial(a_origin, a_dir, b_origin, b_dir) -> bool:
    return abs(_dot(a_dir, b_dir)) > 0.999 and _point_to_line_dist(b_origin, a_origin, a_dir) <= TOL_LEN


def _hole_positioned_by_geometry(origin, direction, m: SolidMeasurements) -> str | None:
    """Return why a hole's position follows from the drawn geometry, or None."""
    for arc in m.arcs + m.shafts:
        if not arc.is_concave and arc.angular_span_deg >= 180.0 and _coaxial(arc.axis_origin, arc.axis_dir, origin, direction):
            return f"współosiowy z łukiem/walcem R{arc.radius:g}"
    for s in m.shafts:  # cross hole through a turned part, centred in the shaft section
        if abs(_dot(s.axis_dir, direction)) < 1e-3:
            # closest approach between the two axes
            if _point_to_line_dist(origin, s.axis_origin, s.axis_dir) <= 0.05 or _axes_intersect(origin, direction, s):
                t = _dot(_sub(origin, s.axis_origin), s.axis_dir)
                t_mid = (s.axial_min + s.axial_max) / 2.0 - _dot(s.axis_origin, s.axis_dir)
                if abs(t - t_mid) <= 0.05:
                    return f"poprzeczny, w połowie walca Ø{s.diameter:g}"
    return None


def _axes_intersect(origin, direction, s: MeasuredCylinder) -> bool:
    n = _cross(direction, s.axis_dir)
    nn = _norm(n)
    if nn < 1e-9:
        return False
    return abs(_dot(_sub(origin, s.axis_origin), n)) / nn <= 0.05


def _step_position_ambiguities(spec: PartSpec, m: SolidMeasurements, dims: list[ExtractedDimension]) -> list[Ambiguity]:
    """Turned parts: every shoulder (plane face across the main axis) must sit at a distance
    from the part's end that follows exactly from the drawing's numbers (a, a+-b, a+-b+-c).

    The view comparison confirms positions only to the drawing's precision (~0.05 mm), so a
    shoulder at 9.93 instead of 310-150-150 = 10 would otherwise pass unnoticed."""
    if not m.shafts or not dims:
        return []
    main = max(m.shafts, key=lambda s: s.axial_length)
    ax = main.axis_dir
    k_ax = max(range(3), key=lambda i: abs(ax[i]))
    if main.axial_length < 0.5 * m.bbox_dims[k_ax]:
        return []  # not a turned part: a boss on a plate is placed by the views
    vals = sorted({round(d.value, 4) for d in dims if d.value and d.dim_type in ("dlugosc", "fazka")})
    if not vals:
        return []
    level = {0.0}
    combos = {0.0}
    for _ in range(4):  # signed sums of up to four drawing numbers
        level = {round(x + s * v, 4) for x in level for v in vals for s in (1, -1)}
        combos |= {abs(x) for x in level}
    combos_arr = np.array(sorted(combos))
    t_all = [_dot(c, ax) for c in [(x, y, z) for x in (m.bbox_min[0], m.bbox_max[0])
                                     for y in (m.bbox_min[1], m.bbox_max[1]) for z in (m.bbox_min[2], m.bbox_max[2])]]
    t_lo, t_hi = min(t_all), max(t_all)
    out = []
    for t in sorted({round(p, 4) for p in m.shoulder_positions(ax)}):
        rel = min(t - t_lo, t_hi - t)
        if rel <= TOL_LEN:
            continue
        nearest = float(combos_arr[np.argmin(np.abs(combos_arr - rel))])
        if abs(nearest - rel) > TOL_LEN and abs(nearest - (t - t_lo)) > TOL_LEN:
            out.append(Ambiguity(
                id="PROG-POLOZENIE-STOPNIA",
                description=(f"Stopień w odległości {t - t_lo:.3f} mm od końca części nie wynika dokładnie z wymiarów "
                             f"rysunku (najbliższa wartość z wymiarów: {nearest:g} mm)."),
                assumption="Położenie przyjęte przez AI zgadza się z rysunkiem tylko w granicach jego dokładności.",
                severity="ostrzezenie",
            ))
    return out


def _coordinate_dimensioned(pts, i, m, spec, wys_ids) -> bool:
    """True when an axis-distance dimension equals the holes' distance to a bbox face along i."""
    vals = {b.expected_value for b in spec.dimension_bindings if b.dim_id in wys_ids and b.expected_value}
    for q in pts:
        for face in (m.bbox_min[i], m.bbox_max[i]):
            if any(abs(abs(q[i] - face) - v) <= TOL_LEN for v in vals):
                return True
    return False


# added by the interpreter (not by the AI's judgement), so they keep source="program"
PROGRAM_IDS_FROM_INTERPRETER = {"AI-BEZ-OBRAZU"}


def _drawing_number_sums(dims: list[ExtractedDimension], terms: int = 4) -> np.ndarray:
    """All |signed sums| of up to `terms` drawing numbers (lengths, diameters, radii, 2R)."""
    vals: set[float] = set()
    for d in dims:
        if not d.value:
            continue
        if d.dim_type in ("dlugosc", "srednica", "gwint", "kwadrat", "fazka"):
            vals.add(round(d.value, 4))
        elif d.dim_type == "promien":
            vals.update((round(d.value, 4), round(2 * d.value, 4)))
    level, combos = {0.0}, {0.0}
    for _ in range(terms):
        level = {round(x + s * v, 4) for x in level for v in vals for s in (1, -1)}
        combos |= {abs(x) for x in level}
    return np.array(sorted(combos))


def _extent_ambiguities(m: SolidMeasurements, dims: list[ExtractedDimension]) -> list[Ambiguity]:
    """Overall sizes must follow exactly from the drawing's numbers. The views confirm them
    only to the drawing's precision; an extent like 149.09 instead of 125 + 2 x R12 = 149
    is reported as uncertain."""
    if not dims:
        return []
    sums = _drawing_number_sums(dims)
    out = []
    for ax, ext in zip("XYZ", m.bbox_dims):
        nearest = float(sums[np.argmin(np.abs(sums - ext))])
        if abs(nearest - ext) > TOL_LEN:
            out.append(Ambiguity(
                id=f"PROG-GABARYT-{ax}",
                description=f"Gabaryt modelu {ext:.3f} mm ({ax}) nie wynika dokładnie z wymiarów rysunku "
                            f"(najbliższa wartość z wymiarów: {nearest:g} mm).",
                assumption="Wymiar przyjęty przez AI zgadza się z rysunkiem tylko w granicach jego dokładności.",
                severity="ostrzezenie",
            ))
    return out


def _signed_sums(vals: set[float], terms: int) -> np.ndarray:
    """All |signed sums| of up to `terms` of the values (sorted, rounded to 1e-4)."""
    v = np.array(sorted(vals))
    v = np.concatenate([v, -v])
    level = np.zeros(1)
    combos = [level]
    for _ in range(terms):
        level = np.unique(np.round(np.add.outer(level, v).ravel(), 4))
        combos.append(np.abs(level))
    return np.unique(np.concatenate(combos))


def _hole_coordinate_ambiguities(spec: PartSpec, m: SolidMeasurements, dims: list[ExtractedDimension]) -> list[Ambiguity]:
    """Every hole (round, square, slot end) must sit at a distance from the part's faces that
    follows exactly from the drawing's numbers (signed sums of up to four numbers or their
    halves, for symmetric placement). A pattern dimension is satisfied by one hole of the
    pattern; the others are confirmed by the views only to the drawing's precision (~0.1 mm),
    so a single hole 0.07 mm off would otherwise pass unnoticed."""
    vals: set[float] = set()
    for d in dims:
        if not d.value:
            continue
        if d.dim_type in ("dlugosc", "srednica", "gwint", "kwadrat", "fazka"):
            vals.add(round(d.value, 4))
        elif d.dim_type == "promien":
            vals.update((round(d.value, 4), round(2 * d.value, 4)))
    if not vals:
        return []
    vals |= {round(v / 2.0, 4) for v in vals}
    sums = _signed_sums(vals, 4)

    feats: list[tuple[tuple, tuple, str]] = [(h.axis_origin, h.axis_dir, f"Ø{h.diameter:g}") for h in m.holes]
    feats += [(a.axis_origin, a.axis_dir, f"R{a.radius:g} (koniec fasolki)") for a in m.arcs
              if a.is_concave and a.angular_span_deg >= 170.0]
    for op in spec.operations:
        if getattr(op, "op", "") == "otwor_kwadratowy":
            for u, v in op.positions:
                origin, direction = {
                    "XY": ((u, v, 0.0), (0.0, 0.0, 1.0)),
                    "XZ": ((u, 0.0, v), (0.0, 1.0, 0.0)),
                    "YZ": ((0.0, u, v), (1.0, 0.0, 0.0)),
                }[op.plane]
                feats.append((origin, direction, f"□{op.size:g}"))

    # holes on a dimensioned pitch circle (three or more): placed by the circle, not by coordinates
    pitch = [ext.value for b in spec.dimension_bindings if b.target == "otwor_rozstaw"
             for ext in dims if ext.id == b.dim_id and ext.dim_type == "srednica" and ext.value]
    on_pitch = set()
    if pitch:
        for g in _pitch_circle_groups(m.holes):
            if any(abs(2 * g[1] - p) <= TOL_LEN for p in pitch):
                on_pitch.update(id(h) for h in g[0])

    bad = []
    for (origin, direction, desc), h in zip(feats, m.holes + [None] * (len(feats) - len(m.holes))):
        k_ax = max(range(3), key=lambda i: abs(direction[i]))
        if abs(direction[k_ax]) < 0.999 or (h is not None and id(h) in on_pitch):
            continue
        if _hole_positioned_by_geometry(origin, direction, m) is not None:
            continue
        for i in range(3):
            if i == k_ax:
                continue
            rel = origin[i] - m.bbox_min[i]
            near = float(sums[np.argmin(np.abs(sums - rel))])
            near2 = float(sums[np.argmin(np.abs(sums - (m.bbox_max[i] - origin[i])))])
            if abs(near - rel) > TOL_LEN and abs(near2 - (m.bbox_max[i] - origin[i])) > TOL_LEN:
                bad.append(f"{desc}: {'XYZ'[i]} = {rel:.3f} mm od krawędzi (najbliższa wartość z wymiarów: {near:g} mm)")
    if not bad:
        return []
    return [Ambiguity(
        id="PROG-POLOZENIE-OTWORU",
        description=("Położenie otworu nie wynika dokładnie z wymiarów rysunku: " + "; ".join(sorted(set(bad))[:4])
                     + ("" if len(set(bad)) <= 4 else f" (i {len(set(bad)) - 4} więcej)") + "."),
        assumption="Położenie przyjęte przez AI zgadza się z rysunkiem tylko w granicach jego dokładności.",
        severity="ostrzezenie",
    )]


def _near_symmetry_errors(holes, centre, m=None, spec=None, wys_ids=()) -> list[str]:
    """Holes (or hole patterns) drawn on a symmetry plane must sit on it exactly.

    The drawing resolves positions only to its own precision (~0.05 mm), so a pattern a few
    hundredths off the centre would pass the view comparison; the symmetric position is the
    only one the drawing can mean."""
    out = []
    groups: dict[tuple, list] = {}
    for o, d, desc in holes:
        key = tuple(int(round(abs(c))) for c in d) + (desc,)
        groups.setdefault(key, []).append(o)
    for (dx, dy, dz, desc), pts in groups.items():
        for i, dcomp in enumerate((dx, dy, dz)):
            if dcomp:  # along the hole axis
                continue
            mid = (min(q[i] for q in pts) + max(q[i] for q in pts)) / 2.0
            off = abs(mid - centre[i])
            if m is not None and _coordinate_dimensioned(pts, i, m, spec, wys_ids):
                continue  # a dimension places the holes along this axis
            if TOL_LEN < off <= 3 * TOL_VIEW_MM:
                out.append(
                    f"Otwory {desc} leżą {off:.3f} mm obok osi symetrii części ({'XYZ'[i]} = {centre[i]:g}); "
                    "rysunek pokazuje je symetrycznie, więc muszą leżeć dokładnie na osi."
                )
    return out


def detect_program_ambiguities(
    spec: PartSpec, m: SolidMeasurements, ai_ambiguities: list[Ambiguity], sym_errors: list[str] | None = None
) -> list[Ambiguity]:
    """Ambiguities found by the program itself, independent of what the AI reported.

    A hole needs its position from the drawing: a dimension (spacing / axis distance),
    or a geometric relation that the drawing shows unambiguously (concentric with an end
    arc of >= 180 deg, or a cross hole centred in a shaft). The model position is always
    verified against the drawn geometry by the view comparison; this rule reports that the
    position is *scaled from the drawing* rather than dimensioned.
    """
    hole_dim_ids = [b.dim_id for b in spec.dimension_bindings if b.target in ("otwor_srednica", "gwint_wewnetrzny", "kwadrat")]
    pos_targets = {b.target for b in spec.dimension_bindings if b.target in HOLE_POSITION_TARGETS}

    holes = [(h.axis_origin, h.axis_dir, f"Ø{h.diameter:g}") for h in m.holes]
    for op in spec.operations:  # square holes are verified in the views; here only their position
        if getattr(op, "op", "") == "otwor_kwadratowy":
            for u, v in op.positions:
                origin, direction = {
                    "XY": ((u, v, 0.0), (0.0, 0.0, 1.0)),
                    "XZ": ((u, 0.0, v), (0.0, 1.0, 0.0)),
                    "YZ": ((0.0, u, v), (1.0, 0.0, 0.0)),
                }[op.plane]
                holes.append((origin, direction, f"□{op.size:g}"))

    found: list[Ambiguity] = []
    centre = tuple((m.bbox_min[i] + m.bbox_max[i]) / 2.0 for i in range(3))
    if sym_errors is None:
        sym_errors = []

    # V-notches: no dimension can place them, so they must sit on the part's symmetry plane
    for op in spec.operations:
        if getattr(op, "op", "") == "wyciecie_v":
            k = 0 if op.plane in ("XZ", "XY") else 1
            if abs(op.center_x - centre[k]) > TOL_LEN:
                found.append(Ambiguity(
                    id="PROG-POLOZENIE-WYCIECIA",
                    description="Położenie wycięcia V poza osią symetrii części nie wynika z żadnego wymiaru.",
                    assumption=f"Środek wycięcia przyjęty przez AI w {op.center_x:g} mm (oś części: {centre[k]:g} mm).",
                    severity="blad_rysunku",
                ))

    unpositioned = [(o, d, desc) for o, d, desc in holes if _hole_positioned_by_geometry(o, d, m) is None]
    wys = [b.dim_id for b in spec.dimension_bindings if b.target == "otwor_wysokosc"]
    sym_errors.extend(_near_symmetry_errors(holes, centre, m, spec, wys))
    if unpositioned:
        # two in-plane coordinates are needed: axis distances, a spacing, or the symmetry plane
        coords = sum(1 for b in spec.dimension_bindings if b.target == "otwor_wysokosc")
        if "otwor_rozstaw" in pos_targets and len(unpositioned) > 1:
            coords += 1
        o0, d0, _ = unpositioned[0]
        in_plane = [i for i in range(3) if abs(d0[i]) < 0.5]
        pts = [o for o, _, _ in unpositioned]
        for i in in_plane:
            mid = (min(p[i] for p in pts) + max(p[i] for p in pts)) / 2.0
            if abs(mid - centre[i]) <= TOL_LEN:
                coords += 1
        missing = coords < 2
        if missing:  # always reported by the program itself, whatever the AI said
            found.append(Ambiguity(
                id="PROG-POLOZENIE-OTWOROW",
                description=(
                    f"Rysunek nie wymiaruje położenia otworów {', '.join(desc for _, _, desc in unpositioned)}: "
                    f"brak wymiarów ustalających obie współrzędne (rozstaw, odległość osi, symetria)."
                ),
                assumption=(
                    "Położenie przyjęte przez AI zostało sprawdzone z geometrią rysunku (porównanie widoków, "
                    f"tolerancja {TOL_VIEW_MM} mm), ale nie wynika z wymiaru. Do potwierdzenia przez konstruktora."
                ),
                severity="blad_rysunku",
                affected_dimensions=hole_dim_ids,
            ))
    return found


def _prism_thickness_dimensioned(shape, k: int, m: SolidMeasurements, spec: PartSpec,
                                 ext_by_id: dict[str, ExtractedDimension]) -> bool:
    """A part seen only along axis k is fully defined by that view when it is a straight prism
    along k (every plane across k is one of the two end faces, every other face runs along k)
    and its extent along k is a thickness / overall dimension of the drawing (e.g. 'g=3')."""
    ext = m.bbox_dims[k]
    matching = [
        b for b in spec.dimension_bindings
        if b.target in ("grubosc", "gabaryt") and b.dim_id in ext_by_id and ext_by_id[b.dim_id].value is not None
        and abs(ext_by_id[b.dim_id].value - ext) <= TOL_LEN
    ]
    # the same number may already describe an extent inside the view (e.g. 80 x 50 plate drawn
    # with thickness 50): the depth needs a dimension of its own
    same_in_view = sum(1 for i in range(3) if i != k and abs(m.bbox_dims[i] - ext) <= TOL_LEN)
    if len(matching) <= same_in_view:
        return False
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        exp.Next()
        surf = BRepAdaptor_Surface(face, True)
        st = surf.GetType()
        if st == GeomAbs_Plane:
            d = surf.Plane().Axis().Direction()
            n = (d.X(), d.Y(), d.Z())
            if abs(n[k]) < 1e-6:
                continue  # a side face along k
            if abs(n[k]) < 1 - 1e-6:
                return False  # tilted (chamfer, slope across k)
            p = surf.Plane().Location()
            pk = (p.X(), p.Y(), p.Z())[k]
            if min(abs(pk - m.bbox_min[k]), abs(pk - m.bbox_max[k])) > TOL_LEN:
                return False  # a step / pocket floor inside the thickness
        elif st == GeomAbs_Cylinder:
            d = surf.Cylinder().Axis().Direction()
            if abs((d.X(), d.Y(), d.Z())[k]) < 1 - 1e-6:
                return False  # a hole or rounding across k
        else:
            return False
    return True


def verify_part(
    spec: PartSpec,
    solid: cq.Workplane,
    extracted_dimensions: list[ExtractedDimension],
    attempts: int = 1,
    step_path: str | None = None,
    image_path: str | None = None,
    views: list | None = None,
    sheet_scale: float | None = None,
) -> VerificationReport:
    """Measure the built solid, check off EVERY extracted PDF dimension (rule 4) and compare
    the model with the drawn views. Without drawn views the result is at best NIEPEWNE."""
    m = measure_solid(solid)
    ext_by_id = {d.id: d for d in extracted_dimensions}
    bound_ids: set[str] = set()
    errors: list[str] = []

    if not m.is_valid:
        errors.append("Bryła 3D jest niepoprawna (isValid() == False).")
    if m.n_solids != 1:
        errors.append(f"Model składa się z {m.n_solids} osobnych brył zamiast jednej.")
    if m.n_shells > m.n_solids:
        errors.append("Bryła ma wewnętrzne puste przestrzenie, których nie widać na rysunku.")
    partial_amb: list[Ambiguity] = []
    hole_dims = [ext_by_id[b.dim_id].value for b in spec.dimension_bindings
                 if b.target in ("otwor_srednica", "gwint_wewnetrzny") and b.dim_id in ext_by_id]
    for h in m.holes:
        def covers(v):
            if v is None:
                return False
            pitch = ISO_PITCH_MM.get(int(round(v))) if abs(v - round(v)) < 1e-6 else None
            core = v - 1.0825 * pitch if pitch else None
            return abs(h.diameter - v) <= TOL_LEN or (core is not None and core - TOL_LEN <= h.diameter <= core + 0.2)
        if not any(covers(v) for v in hole_dims):
            errors.append(f"Otwór Ø{h.diameter:g} w modelu nie odpowiada żadnemu wymiarowi otworu ani gwintu na rysunku.")
    depth_dims = [ext_by_id[b.dim_id].value for b in spec.dimension_bindings
                  if b.target == "glebokosc_otworu" and b.dim_id in ext_by_id]
    for h in m.blind_holes:
        if any(d is not None and abs(h.axial_length - d) <= TOL_LEN for d in depth_dims):
            continue
        errors.append(
            f"Otwór Ø{h.diameter:g} kończy się w materiale (głębokość {h.axial_length:.2f} mm), a rysunek nie podaje "
            "głębokości: bez wymiaru głębokości otwór jest przelotowy."
        )
    for h in m.partial_holes:
        partial_amb.append(Ambiguity(
            id="PROG-OTWOR-CZESCIOWY",
            description=(f"Otwór Ø{h.diameter:g} przechodzi tylko przez część ścianek leżących na jego osi; "
                         "rysunek bez linii niewidocznych tego nie rozstrzyga."),
            assumption="Przyjęto otwór tylko w ściankach zbudowanych przez AI. Do potwierdzenia.",
            severity="ostrzezenie",
        ))
    for op in spec.operations:  # square holes: same through-check along their axis
        if getattr(op, "op", "") != "otwor_kwadratowy":
            continue
        for u, v in op.positions:
            origin, direction = {
                "XY": ((u, v, 0.0), (0.0, 0.0, 1.0)),
                "XZ": ((u, 0.0, v), (0.0, 1.0, 0.0)),
                "YZ": ((0.0, u, v), (1.0, 0.0, 0.0)),
            }[op.plane]
            shape = solid.val().wrapped
            # cut direction of the block: XY -> +Z, XZ -> -Y, YZ -> +X
            cut_dir = {"XY": (0.0, 0.0, 1.0), "XZ": (0.0, -1.0, 0.0), "YZ": (1.0, 0.0, 0.0)}[op.plane]
            has_bottom = op.depth is not None and _is_point_inside(
                shape, tuple(origin[i] + (op.depth + 0.05) * cut_dir[i] for i in range(3)))
            if has_bottom and not any(d is not None and abs(op.depth - d) <= TOL_LEN for d in depth_dims):
                errors.append(f"Otwór □{op.size:g} kończy się w materiale (głębokość {op.depth:g} mm), a rysunek nie podaje głębokości.")
            elif not has_bottom and _axis_has_material(shape, origin, direction, m):
                partial_amb.append(Ambiguity(
                    id="PROG-OTWOR-CZESCIOWY",
                    description=f"Otwór □{op.size:g} przechodzi tylko przez część ścianek leżących na jego osi.",
                    assumption="Przyjęto otwór tylko w ściankach zbudowanych przez AI. Do potwierdzenia.",
                    severity="ostrzezenie",
                ))

    sym_errors: list[str] = []
    ambiguities = list(spec.ambiguities) + detect_program_ambiguities(spec, m, list(spec.ambiguities), sym_errors)
    ambiguities.extend(partial_amb)
    ambiguities.extend(_step_position_ambiguities(spec, m, extracted_dimensions))
    ambiguities.extend(_extent_ambiguities(m, extracted_dimensions))
    ambiguities.extend(_hole_coordinate_ambiguities(spec, m, extracted_dimensions))
    errors.extend(sym_errors)

    view_checks: list[ViewCheck] = []
    if views:
        from drawing2cad.drawing_compare import compare_views

        vresults = compare_views(solid.val().wrapped, views, extracted_dimensions, sheet_scale)
        covered = {i for vr in vresults if vr.frame for vec in vr.frame[1:] for i in range(3) if vec[i]}
        for k in sorted({0, 1, 2} - covered) if covered else []:
            # no view shows extent k: only a part turned about a drawn axis has it fixed (= its diameter)
            turning = [sh for sh in m.shafts if abs(sh.axis_dir[k]) < 1e-6 and any(abs(sh.axis_dir[i]) > 0.999 for i in covered)]
            extent = m.bbox_dims[k]
            if turning:
                main_t = max(turning, key=lambda sh: sh.diameter)
                d_max = main_t.diameter
                if extent > d_max + TOL_LEN:
                    errors.append(
                        f"Model ma {extent:.2f} mm w kierunku {'XYZ'[k]}, którego nie pokazuje żaden widok; "
                        f"dla części toczonej powinien mieć tyle, ile największa średnica (Ø{d_max:g})."
                    )
                else:
                    from drawing2cad.drawing_compare import model_edge_points

                    pts3 = model_edge_points(solid.val().wrapped)
                    o, d = np.array(main_t.axis_origin), np.array(main_t.axis_dir)
                    rel = pts3 - o
                    radial = np.linalg.norm(rel - np.outer(rel @ d, d), axis=1) if len(pts3) else np.zeros(0)
                    if len(radial) and radial.max() > d_max / 2.0 + TOL_LEN:
                        errors.append(
                            f"Krawędź modelu leży {radial.max():.2f} mm od osi toczenia, poza największym promieniem "
                            f"{d_max / 2:g} mm; w kierunku {'XYZ'[k]} żaden widok tego nie pokazuje."
                        )
            elif _prism_thickness_dimensioned(solid.val().wrapped, k, m, spec, ext_by_id):
                continue  # a plate of constant thickness drawn in one view, the thickness dimensioned
            else:
                ambiguities.append(Ambiguity(
                    id=f"BRAK-WIDOKU-{'XYZ'[k]}",
                    description=f"Żaden widok nie pokazuje części w kierunku {'XYZ'[k]}; wymiar {extent:.2f} mm niezweryfikowany.",
                    assumption="Przyjęto wymiar z interpretacji AI.",
                    severity="ostrzezenie",
                ))
        for vr in vresults:
            view_checks.append(
                ViewCheck(view_index=vr.view_index, ok=vr.ok, uncertain=vr.uncertain, projection=vr.projection,
                          scale=vr.scale, message=vr.message())
            )
            if vr.ok:
                continue
            if vr.uncertain:
                ambiguities.append(Ambiguity(
                    id=f"WIDOK-{vr.view_index}-NIEPEWNY",
                    description=f"Widoku {vr.view_index} nie da się jednoznacznie porównać z modelem.",
                    assumption=vr.message(),
                    severity="ostrzezenie",
                ))
            else:
                errors.append(f"Model niezgodny z rysunkiem. {vr.message()}")
    else:
        ambiguities.append(
            Ambiguity(
                id="BRAK-POROWNANIA-Z-RYSUNKIEM",
                description="Nie porównano modelu z geometrią widoków rysunku (brak linii wektorowych widoków).",
                assumption="Sprawdzono tylko wymiary liczbowe; układ elementów niezweryfikowany.",
                severity="ostrzezenie",
            )
        )

    if odd := [d for d in extracted_dimensions if d.type_uncertain]:
        ambiguities.append(Ambiguity(
            id="PROG-RODZAJ-WYMIARU-NIEPEWNY",
            description="Przed wartością stoi symbol narysowany krzywymi, którego program nie odczytał: "
                        + ", ".join(f"{d.id} '{d.decoded_text}'" for d in odd)
                        + ". Może to być średnica, kwadrat itp., a nie długość.",
            assumption="Rodzaj tych wymiarów nie jest pewny.",
            severity="ostrzezenie",
            affected_dimensions=[d.id for d in odd],
            source="program",
        ))

    uncertain_dims: set[str] = set()
    has_blocking_ambiguity = False
    for amb in ambiguities:
        if amb.severity in ("blad_rysunku", "ostrzezenie"):
            has_blocking_ambiguity = True
            uncertain_dims.update(amb.affected_dimensions)

    checks: list[DimensionCheck] = []
    valid_bindings: list[DimensionBinding] = []
    used_threads: set[int] = set()
    for binding in spec.dimension_bindings:
        if binding.dim_id not in ext_by_id:
            errors.append(f"Powiązanie odwołuje się do nieistniejącego wymiaru PDF: {binding.dim_id}.")
            continue
        if binding.dim_id in bound_ids:
            errors.append(f"Wymiar {binding.dim_id} powiązany więcej niż raz.")
            continue
        bound_ids.add(binding.dim_id)
        ext = ext_by_id[binding.dim_id]

        if binding.expected_value is not None and ext.value is not None and abs(binding.expected_value - ext.value) > 1e-6:
            msg = f"Naruszenie zasady 1: AI zmieniło liczbę z rysunku ({ext.value:g} -> {binding.expected_value:g})."
            checks.append(DimensionCheck(
                dim_id=binding.dim_id, raw_text=ext.decoded_text, target=binding.target,
                expected=f"{ext.value:g}", measured=f"AI podało {binding.expected_value:g}",
                status="BŁĄD", message=msg,
            ))
            errors.append(msg)
            continue

        chk = _check_binding(binding, ext, m, spec, uncertain_dims, used_threads)
        checks.append(chk)
        if chk.status == "BŁĄD":
            errors.append(f"[{chk.dim_id} '{chk.raw_text}'] {chk.message}")
        else:
            valid_bindings.append(binding)

    for ids, msg in _feature_count_errors(valid_bindings, ext_by_id, m):
        errors.append(msg)
        for c in checks:
            if c.dim_id in ids:
                c.status = "BŁĄD"
                c.message = (c.message + " " if c.message else "") + msg

    unused_dims = [d.id for d in extracted_dimensions if d.id not in bound_ids]
    for uid in unused_dims:
        ext = ext_by_id[uid]
        msg = f"Nieużyty wymiar z rysunku: {uid} ('{ext.decoded_text}')."
        checks.append(DimensionCheck(
            dim_id=uid, raw_text=ext.decoded_text, target="nieuzyty", expected=ext.decoded_text,
            measured="brak powiązania", status="BŁĄD", message=msg,
        ))
        errors.append(msg)

    # provenance: only what the AI wrote is "ai"; everything the program added is "program"
    ai_ids = {id(a) for a in spec.ambiguities if a.id not in PROGRAM_IDS_FROM_INTERPRETER}
    ambiguities = [a.model_copy(update={"source": "ai" if id(a) in ai_ids else "program"}) for a in ambiguities]

    # An AI warning about how to READ the drawing is settled when the program verified the
    # model against everything the drawing shows and found nothing: every dimension, every
    # view (one orientation, no unexplained edge, no hidden rounding), and no doubt of its own.
    # What the drawing cannot show is covered by the program's own checks, so it stays NIEPEWNE.
    fully_verified = (
        bool(view_checks)
        and all(v.ok for v in view_checks)
        and not errors
        and all(c.status != "BŁĄD" for c in checks)
        and not unused_dims
        and not any(a.source == "program" and a.severity in ("blad_rysunku", "ostrzezenie") for a in ambiguities)
    )
    if fully_verified:
        ambiguities = [
            a.model_copy(update={
                "severity": "info",
                "assumption": a.assumption + " [Rozstrzygnięte: model zgodny ze wszystkimi wymiarami i widokami rysunku.]",
            }) if a.source == "ai" and a.severity == "ostrzezenie" else a
            for a in ambiguities
        ]
        for c in checks:
            if c.status == "NIEPEWNE" and not any(
                a.severity in ("blad_rysunku", "ostrzezenie") and c.dim_id in a.affected_dimensions for a in ambiguities
            ):
                c.status = "OK"
        has_blocking_ambiguity = any(a.severity in ("blad_rysunku", "ostrzezenie") for a in ambiguities)

    has_error = bool(errors) or any(c.status == "BŁĄD" for c in checks)
    has_uncertain = has_blocking_ambiguity or any(c.status == "NIEPEWNE" for c in checks)
    overall: CheckStatus = "BŁĄD" if has_error else ("NIEPEWNE" if has_uncertain else "OK")

    return VerificationReport(
        drawing_no=spec.drawing_no,
        name=spec.name,
        status=overall,
        passed=not has_error,
        bounding_box_mm=m.bbox_dims,
        volume_mm3=round(m.volume, 2),
        is_valid_solid=m.is_valid,
        checks=checks,
        view_checks=view_checks,
        unused_dimensions=unused_dims,
        ambiguities=ambiguities,
        threads=list(spec.threads),
        errors=errors,
        attempts=attempts,
        step_path=step_path,
        image_path=image_path,
    )
