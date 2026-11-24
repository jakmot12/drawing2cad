"""Compare the built 3D model with the drawing's own vector geometry (rule 4, layout part).

Dimension checks only prove that a number exists *somewhere* in the model. This module
proves that the model *looks like the drawing*: every visible edge drawn in each view must
lie on a visible edge of the model's orthographic projection and vice versa.

Drawing side (deterministic, from the vector PDF):
  - line widths are classified per page: the thickest common width = visible edges
    (required), widths between thin and visible = conventional thread lines (optional),
    the thinnest = dimensions / centre lines / hatching (ignored); the sheet frame and
    everything attached to it (title block) is ignored,
  - views are clusters of visible edges; a view split into collinear pieces by small gaps
    is a broken view (przerwanie widoku),
  - views with hatching (sections) or axonometric line directions are compared too, but a
    mismatch there gives NIEPEWNE instead of BŁĄD.

Model side: OpenCASCADE hidden-line removal (HLR) along +-X, +-Y, +-Z, sharp edges and
silhouettes only (tangent edges are hidden, as in Inventor's default drawing style).

All views of one part must be explained by ONE model orientation and ONE scale, placed
according to first-angle (European) or third-angle projection; this rejects mirrored parts
and views matched in contradictory orientations.
"""
from __future__ import annotations

import itertools
import math
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pymupdf
from scipy.spatial import cKDTree

PT_TO_MM = 25.4 / 72.0

SAMPLE_PT = 0.2  # sampling step along drawing primitives [pt] (~0.07 mm)
MODEL_STEP_MM = 0.05  # sampling step along model edges [mm]
JOIN_PT = 2.0  # primitives closer than this belong to one view [pt]
BREAK_GAP_PT = 15.0  # max gap between pieces of a broken view [pt]
BREAK_ZONE_PT = 10.0  # half-width of the skipped zone around a break gap [pt]

# Inventor writes vector coordinates far more precisely than this; the value only has to
# exceed the sampling distance between two discretised curves (~0.04 mm).
TOL_MM = 0.1
MAX_UNMATCHED_POINTS = 2
TORUS_MIN_MERIDIAN_PTS = 3  # samples of a torus silhouette meridian needed to count it as a profile
# fitted drawn arcs are far more precise than single points (hundreds of samples)
ARC_R_TOL = 0.03
ARC_C_TOL = 0.08  # centres: Inventor's PDF scale is off by ~0.1 %, so only gross shifts

STANDARD_SCALES = [1.0, 2.0, 0.5, 5.0, 0.2, 2.5, 0.4, 10.0, 0.1, 0.25, 4.0]

# ISO metric coarse pitches (thread minor diameter = d - 1.0825 * P)
ISO_PITCH_MM: dict[int, float] = {
    3: 0.5, 4: 0.7, 5: 0.8, 6: 1.0, 8: 1.25, 10: 1.5, 12: 1.75, 14: 2.0,
    16: 2.0, 18: 2.5, 20: 2.5, 22: 2.5, 24: 3.0, 27: 3.0, 30: 3.5, 36: 4.0,
}


# ---------------------------------------------------------------------------
# Page line styles and frame
# ---------------------------------------------------------------------------


@dataclass
class PageStyles:
    visible: tuple[float, float]  # stroke width range of visible edges [pt]
    thread: tuple[float, float] | None  # stroke width range of thread lines [pt]
    thin_max: float  # widest "thin" stroke (dimensions, centre lines, hatching)
    frame: tuple[float, float, float, float] | None  # sheet frame rectangle


def _is_dashed(d: dict) -> bool:
    dashes = d.get("dashes")
    return bool(dashes) and dashes.strip() not in ("[] 0", "[]0", "[ ] 0")


def _frame_of(drawings: list[dict], page_w: float, page_h: float):
    """Sheet frame: one large rectangle path, or long horizontal + vertical lines forming one."""
    best = None
    for d in drawings:
        if d.get("type") != "s":
            continue
        r = d["rect"]
        if r.width >= 0.6 * page_w and r.height >= 0.6 * page_h:
            if best is None or r.width * r.height > (best[2] - best[0]) * (best[3] - best[1]):
                best = (r.x0, r.y0, r.x1, r.y1)
    if best:
        return best
    hs, vs = [], []
    for d in drawings:
        for a, b in _segments(d):
            if abs(a[1] - b[1]) < 0.5 and abs(a[0] - b[0]) >= 0.6 * page_w:
                hs.append((min(a[0], b[0]), a[1], max(a[0], b[0])))
            elif abs(a[0] - b[0]) < 0.5 and abs(a[1] - b[1]) >= 0.6 * page_h:
                vs.append((a[0], min(a[1], b[1]), max(a[1], b[1])))
    if len(hs) >= 2 and len(vs) >= 2:
        return (min(v[0] for v in vs), min(h[1] for h in hs), max(v[0] for v in vs), max(h[1] for h in hs))
    return None


def page_styles(drawings: list[dict], page_w: float, page_h: float) -> PageStyles | None:
    """Classify stroke widths of one page (frame and title block excluded).

    visible = the thick width carrying the most line length; thread lines = solid strokes
    between 60 % and 95 % of it; everything thinner (dimensions, axes, hatching) is thin.
    None when visible edges cannot be told apart from thin lines."""
    frame = _frame_of(drawings, page_w, page_h)
    length: Counter[float] = Counter()
    for d in drawings:
        if d.get("type") not in ("s", "fs") or _is_dashed(d):
            continue
        r = d["rect"]
        if touches_frame((r.x0, r.y0, r.x1, r.y1), frame):
            continue
        w = round(d.get("width") or 0.0, 2)
        if w > 0:
            length[w] += sum(math.dist(a, b) for a, b in _segments(d)) + 5.0 * sum(1 for it in d["items"] if it[0] == "c")
    if len(length) < 2:
        return None
    total = sum(length.values())
    significant = [w for w in length if length[w] >= 0.05 * total]  # a few section-plane marks do not count
    w_max = max(significant)
    thick = [w for w in significant if w >= 0.6 * w_max]
    visible = max(thick, key=lambda w: length[w])
    thin = [w for w in length if w <= 0.6 * visible]
    if not thin:
        return None
    mid = [w for w in length if 0.6 * visible < w < 0.95 * visible]
    return PageStyles(
        visible=(visible * 0.95, visible * 1.05),
        thread=(min(mid) * 0.98, max(mid) * 1.02) if mid else None,
        thin_max=max(thin) * 1.02,
        frame=frame,
    )


def touches_frame(bb, frame, margin: float = 3.0) -> bool:
    if frame is None:
        return False
    fx0, fy0, fx1, fy1 = frame
    return (
        abs(bb[0] - fx0) < margin or abs(bb[1] - fy0) < margin
        or abs(bb[2] - fx1) < margin or abs(bb[3] - fy1) < margin
    )


# ---------------------------------------------------------------------------
# Drawing side
# ---------------------------------------------------------------------------


@dataclass
class _Prim:
    pts: np.ndarray  # (n, 2) PDF points, y down
    required: bool

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (
            float(self.pts[:, 0].min()),
            float(self.pts[:, 1].min()),
            float(self.pts[:, 0].max()),
            float(self.pts[:, 1].max()),
        )


@dataclass
class DrawingView:
    """One view of a part on the sheet (coordinates in PDF points)."""

    required_prims: list[np.ndarray]  # visible edges, one sampled polyline per primitive
    optional: np.ndarray  # (m, 2) thread-line sample points
    bbox: tuple[float, float, float, float]
    pieces: list[tuple[float, float, float, float]] = field(default_factory=list)
    break_axis: str | None = None  # "x" or "y" when the view is broken
    kind: str = "rzut"  # "rzut" | "przekroj" | "aksonometria"
    hidden: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))  # dashed hidden-edge lines
    zones_pt: list = field(default_factory=list)  # break-symbol zones along break_axis [pt]

    @property
    def is_broken(self) -> bool:
        return self.break_axis is not None

    def break_zones(self) -> list[tuple[float, float]]:
        """Intervals (along break_axis, PDF pt) covered by the break symbols."""
        return list(self.zones_pt)

    def core_bbox(self) -> tuple[float, float, float, float]:
        """Bounding box of visible edges outside the break-symbol zones."""
        pts = np.vstack(self.required_prims)
        k = 0 if self.break_axis == "x" else 1
        keep = np.ones(len(pts), bool)
        for lo, hi in self.break_zones():
            keep &= ~((pts[:, k] >= lo) & (pts[:, k] <= hi))
        pts = pts[keep] if keep.any() else pts
        return (float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max()))


def _sample_segment(p0, p1) -> np.ndarray:
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    n = max(2, int(math.ceil(np.linalg.norm(p1 - p0) / SAMPLE_PT)) + 1)
    t = np.linspace(0.0, 1.0, n)[:, None]
    return p0 + (p1 - p0) * t


def _sample_bezier(p0, p1, p2, p3) -> np.ndarray:
    ctrl = np.array([p0, p1, p2, p3], float)
    approx_len = sum(np.linalg.norm(ctrl[i + 1] - ctrl[i]) for i in range(3))
    n = max(4, int(math.ceil(approx_len / SAMPLE_PT)) + 1)
    t = np.linspace(0.0, 1.0, n)[:, None]
    mt = 1.0 - t
    return mt**3 * ctrl[0] + 3 * mt**2 * t * ctrl[1] + 3 * mt * t**2 * ctrl[2] + t**3 * ctrl[3]


def _drawing_items_to_points(d: dict) -> list[np.ndarray]:
    """Sampled polylines of one PDF path; consecutive connected line segments form one polyline
    (Inventor writes small circles as 32-gons)."""
    out: list[np.ndarray] = []
    chain: list[np.ndarray] = []

    def flush():
        if chain:
            out.append(np.vstack(chain))
            chain.clear()

    for it in d["items"]:
        kind = it[0]
        if kind == "l":
            seg = _sample_segment((it[1].x, it[1].y), (it[2].x, it[2].y))
            if chain and np.linalg.norm(chain[-1][-1] - seg[0]) > 0.01:
                flush()
            chain.append(seg if not chain else seg[1:])
            continue
        flush()
        if kind == "c":
            out.append(
                _sample_bezier((it[1].x, it[1].y), (it[2].x, it[2].y), (it[3].x, it[3].y), (it[4].x, it[4].y))
            )
        elif kind == "re":
            r = it[1]
            corners = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1), (r.x0, r.y0)]
            out.extend(_sample_segment(a, b) for a, b in zip(corners, corners[1:]))
        elif kind == "qu":
            q = it[1]
            corners = [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
            corners.append(corners[0])
            out.extend(_sample_segment(a, b) for a, b in zip(corners, corners[1:]))
    flush()
    return out


def _segments(d: dict) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    return [((it[1].x, it[1].y), (it[2].x, it[2].y)) for it in d["items"] if it[0] == "l"]


def _bbox_gap(a, b) -> float:
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)


def _union_bbox(boxes) -> tuple[float, float, float, float]:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _contains(outer, inner, margin: float = 1.0) -> bool:
    return (
        inner[0] >= outer[0] - margin and inner[1] >= outer[1] - margin
        and inner[2] <= outer[2] + margin and inner[3] <= outer[3] + margin
    )


def _angle_deg(p, q) -> float:
    return math.degrees(math.atan2(-(q[1] - p[1]), q[0] - p[0])) % 180.0


def _view_kind(bb, req_prims: list[np.ndarray], thin_segments) -> str:
    """'przekroj' when hatched, 'aksonometria' when edges run at 30/150 degrees, else 'rzut'."""
    # hatching = a run of >= 6 parallel thin lines at 45/135 degrees with an even spacing
    for target in (45.0, 135.0, 30.0, 60.0, 120.0, 150.0):
        offsets = []
        for p, q in thin_segments:
            if not _contains(bb, (min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1])), 0.5):
                continue
            a = _angle_deg(p, q)
            if abs(a - target) < 1.0 and math.dist(p, q) > 2:
                rad = math.radians(target)
                offsets.append(-math.sin(rad) * p[0] - math.cos(rad) * p[1])
        offs = sorted(set(round(o, 1) for o in offsets))
        diffs = [b - a for a, b in zip(offs, offs[1:])]
        run = 1
        for d0, d1 in zip(diffs, diffs[1:]):
            run = run + 1 if d0 > 1.0 and abs(d1 - d0) <= 0.15 * d0 else 1
            if run >= 5:  # 5 equal gaps = 6 evenly spaced lines
                return "przekroj"
    total = iso = 0.0
    for pts in req_prims:
        if len(pts) < 2:
            continue
        L = float(np.linalg.norm(pts[-1] - pts[0]))
        if L < 2:
            continue
        a = _angle_deg(pts[0], pts[-1])
        total += L
        if min(abs(a - 30), abs(a - 150)) < 2:
            iso += L
    if total > 0 and iso / total > 0.3 and not _arc_centres(req_prims, [np.ones(len(p), bool) for p in req_prims]):
        return "aksonometria"  # axonometric views show circles as ellipses: a true circle rules it out
    return "rzut"


def _pts_bbox(pts: np.ndarray) -> tuple[float, float, float, float]:
    return (float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max()))


def _outline_span(cluster, boxes, axis: str) -> tuple[float, float] | None:
    """Extent across the break axis of the lines running along it (the part's outline)."""
    if axis == "x":
        ys = [(b[1] + b[3]) / 2 for b in (boxes[i] for i in cluster) if b[3] - b[1] < 0.5 and b[2] - b[0] > 2]
    else:
        ys = [(b[0] + b[2]) / 2 for b in (boxes[i] for i in cluster) if b[2] - b[0] < 0.5 and b[3] - b[1] > 2]
    return (min(ys), max(ys)) if len(ys) >= 2 else None


def _is_break_symbol(b, cluster, boxes, side: str, axis: str) -> bool:
    """A primitive at the piece's end that sticks out beyond the outline on both sides."""
    span = _outline_span(cluster, boxes, axis)
    if span is None:
        return False
    cb = _union_bbox([boxes[i] for i in cluster])
    k0, k1 = (0, 2) if axis == "x" else (1, 3)
    p0, p1 = (1, 3) if axis == "x" else (0, 2)
    at_edge = abs(b[k1] - cb[k1]) < 1.0 if side in ("right", "end") else abs(b[k0] - cb[k0]) < 1.0
    return at_edge and b[p0] < span[0] - 1.0 and b[p1] > span[1] + 1.0


def _break_zones(pieces_idx, boxes, piece_boxes, axis: str) -> list[tuple[float, float]]:
    """Along the break axis: the extent of the break symbols at each gap (the zig-zags that
    stick out of the outline), not a fixed width - so steps next to a break stay checked."""
    k0, k1 = (0, 2) if axis == "x" else (1, 3)
    p0, p1 = (1, 3) if axis == "x" else (0, 2)
    order = sorted(range(len(piece_boxes)), key=lambda i: piece_boxes[i][k0])
    zones = []
    for a_i, b_i in zip(order, order[1:]):
        A, B = piece_boxes[a_i], piece_boxes[b_i]
        lo, hi = A[k1], B[k0]
        for cl, side in ((pieces_idx[a_i], "end"), (pieces_idx[b_i], "start")):
            for i in cl:
                if _is_break_symbol(boxes[i], cl, boxes, side, axis):
                    lo, hi = min(lo, boxes[i][k0]), max(hi, boxes[i][k1])
        if lo == A[k1] and hi == B[k0]:  # no symbol found: fall back to a fixed margin
            lo, hi = A[k1] - BREAK_ZONE_PT, B[k0] + BREAK_ZONE_PT
        zones.append((lo - 0.5, hi + 0.5))
    return zones


def extract_views(pdf_path: str, page_index: int, region: tuple[float, float, float, float]) -> list[DrawingView]:
    """Split the visible-edge geometry of one part region into views."""
    with pymupdf.open(str(pdf_path)) as doc:
        page = doc[page_index]
        drawings = page.get_drawings()
        styles = page_styles(drawings, float(page.rect.width), float(page.rect.height))
    if styles is None:
        return []

    prims: list[_Prim] = []
    thin_segments = []
    rx0, ry0, rx1, ry1 = region

    def inside_region(pts) -> bool:
        return (
            pts[:, 0].min() >= rx0 - 0.5 and pts[:, 0].max() <= rx1 + 0.5
            and pts[:, 1].min() >= ry0 - 0.5 and pts[:, 1].max() <= ry1 + 0.5
        )

    hidden_prims: list[np.ndarray] = []
    for d in drawings:
        if d.get("type") not in ("s", "fs"):
            continue
        w = d.get("width") or 0.0
        if _is_dashed(d):
            dashes = d.get("dashes", "").replace("[", " ").replace("]", " ").split()
            if len(dashes) in (2, 3):  # [d] or [dash gap] + phase: hidden line (dash-dot has more numbers)
                hidden_prims.extend(pts for pts in _drawing_items_to_points(d) if inside_region(pts))
            continue
        if styles.visible[0] <= w <= styles.visible[1]:
            required = True
        elif styles.thread and styles.thread[0] <= w <= styles.thread[1]:
            required = False
        else:
            if w <= styles.thin_max:
                thin_segments.extend(_segments(d))
            continue
        for pts in _drawing_items_to_points(d):
            if inside_region(pts):
                prims.append(_Prim(pts, required))

    req = [p for p in prims if p.required]
    parent = list(range(len(req)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    boxes = [p.bbox for p in req]
    for i in range(len(req)):
        for j in range(i + 1, len(req)):
            if _bbox_gap(boxes[i], boxes[j]) <= JOIN_PT:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(req)):
        groups.setdefault(find(i), []).append(i)
    clusters = [list(g) for g in groups.values()]
    clusters = [c for c in clusters if not touches_frame(_union_bbox([boxes[i] for i in c]), styles.frame)]

    # merge clusters lying inside another cluster's bbox (holes inside an outline)
    changed = True
    while changed:
        changed = False
        cb = [_union_bbox([boxes[i] for i in c]) for c in clusters]
        for a in range(len(clusters)):
            for b in range(len(clusters)):
                if a != b and _contains(cb[a], cb[b]):
                    clusters[a] = clusters[a] + clusters[b]
                    del clusters[b]
                    changed = True
                    break
            if changed:
                break

    # merge collinear pieces separated by a gap into one broken view: small gaps always,
    # larger ones when both facing ends carry break symbols (lines taller than the piece)
    def tall_at(cluster: list[int], side: str) -> bool:
        return any(_is_break_symbol(boxes[i], cluster, boxes, side, "x") for i in cluster)

    views_pieces: list[list[list[int]]] = [[c] for c in clusters]
    changed = True
    while changed:
        changed = False
        vb = [_union_bbox([boxes[i] for c in v for i in c]) for v in views_pieces]
        for a in range(len(views_pieces)):
            for b in range(a + 1, len(views_pieces)):
                A, B = vb[a], vb[b]
                h_overlap = min(A[3], B[3]) - max(A[1], B[1])
                h_min = min(A[3] - A[1], B[3] - B[1])
                gap_x = max(A[0], B[0]) - min(A[2], B[2])
                w_overlap = min(A[2], B[2]) - max(A[0], B[0])
                w_min = min(A[2] - A[0], B[2] - B[0])
                gap_y = max(A[1], B[1]) - min(A[3], B[3])
                side_by_side = 0 < gap_x and h_overlap >= 0.7 * h_min
                stacked = 0 < gap_y and w_overlap >= 0.7 * w_min
                if side_by_side and gap_x > BREAK_GAP_PT:
                    left, right = (a, b) if A[0] < B[0] else (b, a)
                    lc = [i for c in views_pieces[left] for i in c]
                    rc = [i for c in views_pieces[right] for i in c]
                    side_by_side = gap_x <= 3 * BREAK_GAP_PT and tall_at(lc, "right") and tall_at(rc, "left")
                if stacked and gap_y > BREAK_GAP_PT:
                    stacked = False
                if side_by_side or stacked:
                    views_pieces[a] = views_pieces[a] + views_pieces[b]
                    del views_pieces[b]
                    changed = True
                    break
            if changed:
                break

    views: list[DrawingView] = []
    opt = [p for p in prims if not p.required]
    for v in views_pieces:
        piece_boxes = [_union_bbox([boxes[i] for i in c]) for c in v]
        bb = _union_bbox(piece_boxes)
        opt_pts = [p.pts for p in opt if _contains((bb[0] - 3, bb[1] - 3, bb[2] + 3, bb[3] + 3), p.bbox)]
        break_axis = None
        if len(v) > 1:
            xs = sorted(piece_boxes, key=lambda b: b[0])
            break_axis = "x" if all(xs[i + 1][0] > xs[i][2] for i in range(len(xs) - 1)) else "y"
        req_prims = [req[i].pts for c in v for i in c]
        hid = [h for h in hidden_prims if _contains((bb[0] - 3, bb[1] - 3, bb[2] + 3, bb[3] + 3), _pts_bbox(h))]
        zones_pt = _break_zones(v, boxes, piece_boxes, break_axis) if break_axis else []
        views.append(
            DrawingView(
                required_prims=req_prims,
                optional=np.vstack(opt_pts) if opt_pts else np.zeros((0, 2)),
                bbox=bb,
                pieces=piece_boxes,
                break_axis=break_axis,
                kind=_view_kind(bb, req_prims, thin_segments),
                hidden=np.vstack(hid) if hid else np.zeros((0, 2)),
                zones_pt=zones_pt,
            )
        )
    views.sort(key=lambda v: (v.bbox[1], v.bbox[0]))
    return views


def parse_scale(text: str | None) -> float | None:
    """'1:2' -> 0.5, '2:1' -> 2.0."""
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*:\s*(\d+(?:[.,]\d+)?)", text)
    if not m:
        return None
    a, b = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
    return a / b if b else None


# ---------------------------------------------------------------------------
# Model side: hidden-line projections
# ---------------------------------------------------------------------------

# view direction (towards the viewer) -> x direction of the view; v = n x u (never mirrored)
VIEW_DIRS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "+Z": ((0, 0, 1), (1, 0, 0)),
    "-Z": ((0, 0, -1), (-1, 0, 0)),
    "+X": ((1, 0, 0), (0, 1, 0)),
    "-X": ((-1, 0, 0), (0, -1, 0)),
    "+Y": ((0, 1, 0), (0, 0, 1)),
    "-Y": ((0, -1, 0), (0, 0, -1)),
}


@dataclass
class ModelProjection:
    name: str
    pts: np.ndarray  # (n, 2) mm


def _discretize_edges(compound, step: float) -> list[np.ndarray]:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_UniformAbscissa
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    pts: list[np.ndarray] = []
    if compound is None or compound.IsNull():
        return pts
    exp = TopExp_Explorer(compound, TopAbs_EDGE)
    while exp.More():
        edge = TopoDS.Edge_s(exp.Current())
        exp.Next()
        try:
            curve = BRepAdaptor_Curve(edge)
        except Exception:
            continue
        u0, u1 = curve.FirstParameter(), curve.LastParameter()
        ua = GCPnts_UniformAbscissa(curve, step, u0, u1)
        if ua.IsDone() and ua.NbPoints() >= 2:
            params = [ua.Parameter(i) for i in range(1, ua.NbPoints() + 1)]
        else:
            params = list(np.linspace(u0, u1, 8))
        params.append(u1)
        pts.append(np.asarray([(curve.Value(t).X(), curve.Value(t).Y()) for t in params], float))
    return pts


def project_model(shape) -> list[ModelProjection]:
    """Visible sharp edges + silhouettes of the solid seen along the 6 axis directions."""
    return [ModelProjection(name, np.vstack(edges)) for name, edges in project_model_edges(shape).items() if edges]


def project_model_edges(shape) -> dict[str, list[np.ndarray]]:
    """Like project_model, but one sampled polyline per visible edge (e.g. to draw a sheet)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepLib import BRepLib
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.HLRAlgo import HLRAlgo_Projector
    from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape

    # work on a copy: marking tangent edges as smooth must not mutate the caller's solid
    shape = BRepBuilderAPI_Copy(shape).Shape()
    BRepLib.EncodeRegularity_s(shape, 1e-3)
    out: dict[str, list[np.ndarray]] = {}
    for name, (n, x) in VIEW_DIRS.items():
        algo = HLRBRep_Algo()
        algo.Add(shape)
        algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*n), gp_Dir(*x))))
        algo.Update()
        algo.Hide()
        hlr = HLRBRep_HLRToShape(algo)
        edges: list[np.ndarray] = []
        for comp in (hlr.VCompound(), hlr.OutLineVCompound()):
            edges.extend(_discretize_edges(comp, MODEL_STEP_MM))
        out[name] = edges
    return out


# ---------------------------------------------------------------------------
# Comparison of one view with one projection
# ---------------------------------------------------------------------------

_ROT = [
    np.array([[1, 0], [0, 1]], float),
    np.array([[0, -1], [1, 0]], float),
    np.array([[-1, 0], [0, -1]], float),
    np.array([[0, 1], [-1, 0]], float),
]


def view_frame(name: str, rot: int) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    """Model directions of (towards viewer, sheet right, sheet up) for a projection + rotation."""
    n, x = VIEW_DIRS[name]
    n_v, x_v = np.array(n), np.array(x)
    y_v = np.cross(n_v, x_v)
    R = _ROT[rot]
    A = R[0, 0] * x_v + R[0, 1] * y_v
    B = R[1, 0] * x_v + R[1, 1] * y_v
    return tuple(int(v) for v in n_v), tuple(int(round(v)) for v in A), tuple(int(round(v)) for v in B)


@dataclass
class _Eval:
    score: float
    proj: str
    rot: int
    scale: float
    d_pts: np.ndarray
    d_bad: np.ndarray
    m_pts: np.ndarray
    m_bad: np.ndarray
    hidden_bad: int = 0  # model features inside the region hidden by a break
    # data to map any 3D model point onto this view and test it against the drawn lines
    shifts: dict = field(default_factory=dict)
    zones: list = field(default_factory=list)
    axis: int = 0
    ref_tree: object = None
    rings: list = field(default_factory=list)  # (centre, r_lo, r_hi) thread conventions
    hidden_tree: object = None  # dashed hidden lines of the drawing (explain hidden model edges)
    through_lines: list = field(default_factory=list)  # perp coordinates of verified lines in a break
    arc_errors: list = field(default_factory=list)  # radius mismatches of drawn arcs

    @property
    def n_bad(self) -> int:
        return int(self.d_bad.sum() + self.m_bad.sum())

    @property
    def ok(self) -> bool:
        return (
            self.d_bad.sum() <= MAX_UNMATCHED_POINTS
            and self.m_bad.sum() <= MAX_UNMATCHED_POINTS
            and not self.arc_errors
        )

    def frame(self):
        return view_frame(self.proj, self.rot)


def _to_mm(pts_pt: np.ndarray, scale: float) -> np.ndarray:
    out = np.array(pts_pt, float) * (PT_TO_MM / scale)
    out[:, 1] *= -1.0
    return out


def _thread_specs(dims) -> list[tuple[float, float]]:
    """(nominal radius, minor radius) for every thread dimension on the drawing."""
    out = []
    for d in dims:
        if d.dim_type == "gwint" and d.value:
            pitch = ISO_PITCH_MM.get(int(round(d.value))) if abs(d.value - round(d.value)) < 1e-6 else None
            if pitch:
                out.append((d.value / 2.0, (d.value - 1.0825 * pitch) / 2.0))
    return out


def _bbox_of(pts: np.ndarray) -> np.ndarray:
    return np.array([pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()])


def _is_straight(p: np.ndarray) -> bool:
    v = p[-1] - p[0]
    n = np.linalg.norm(v)
    if n < 1e-9:
        return False
    dev = np.abs((p[:, 0] - p[0, 0]) * v[1] - (p[:, 1] - p[0, 1]) * v[0]) / n
    return bool(dev.max() < 0.05)


def _arc_centres(prims: list[np.ndarray], keeps: list[np.ndarray]) -> list[tuple[np.ndarray, float]]:
    """Centres and radii of circular drawing primitives (least-squares circle fit)."""
    out: list[tuple[np.ndarray, float]] = []
    for p, k in zip(prims, keeps):
        if len(p) < 6 or not k.all() or _is_straight(p):
            continue
        A = np.column_stack([2 * p[:, 0], 2 * p[:, 1], np.ones(len(p))])
        b = (p**2).sum(axis=1)
        (cx, cy, c0), *_ = np.linalg.lstsq(A, b, rcond=None)
        r = math.sqrt(max(c0 + cx * cx + cy * cy, 0.0))
        if r > 0.2 and np.abs(np.hypot(p[:, 0] - cx, p[:, 1] - cy) - r).max() < max(0.05, 0.01 * r):
            c = np.array([cx, cy])
            if not any(np.linalg.norm(c - q) < 0.05 and abs(r - rq) < 0.05 for q, rq in out):
                out.append((c, r))
    return out


def _fit_circle(p: np.ndarray):
    """(centre, radius, sweep in degrees) of a least-squares circle, or None if not circular."""
    A = np.column_stack([2 * p[:, 0], 2 * p[:, 1], np.ones(len(p))])
    b = (p**2).sum(axis=1)
    (cx, cy, c0), *_ = np.linalg.lstsq(A, b, rcond=None)
    r = math.sqrt(max(c0 + cx * cx + cy * cy, 0.0))
    if r <= 0.2 or np.abs(np.hypot(p[:, 0] - cx, p[:, 1] - cy) - r).max() > max(0.05, 0.01 * r):
        return None
    ang = np.degrees(np.arctan2(p[:, 1] - cy, p[:, 0] - cx))
    lo, hi = _angle_span(ang)
    return np.array([cx, cy]), r, (hi - lo) % 360.0 or 360.0


def _angle_span(ang: np.ndarray) -> tuple[float, float]:
    """Smallest arc [lo, hi] (degrees, lo may exceed hi across 360) covering all angles."""
    a = np.sort(np.mod(ang, 360.0))
    if len(a) < 2:
        return float(a[0]), float(a[0])
    gaps = np.diff(np.concatenate([a, [a[0] + 360.0]]))
    k = int(np.argmax(gaps))
    if gaps[k] < 5.0:
        return 0.0, 360.0
    return float(a[(k + 1) % len(a)]), float(a[k])


def _in_span(ang: np.ndarray, lo: float, hi: float) -> np.ndarray:
    a = np.mod(ang, 360.0)
    if hi - lo >= 360.0 or (lo == 0.0 and hi == 360.0):
        return np.ones(len(a), bool)
    if lo <= hi:
        return (a >= lo - 1.0) & (a <= hi + 1.0)
    return (a >= lo - 1.0) | (a <= hi + 1.0)


def _circle_coverage(pts: np.ndarray, c: np.ndarray, r_lo: float, r_hi: float) -> float:
    """Fraction of the circumference around c covered by points with radius in [r_lo, r_hi]."""
    rd = np.hypot(pts[:, 0] - c[0], pts[:, 1] - c[1])
    sel = (rd >= r_lo) & (rd <= r_hi)
    if not sel.any():
        return 0.0
    ang = np.degrees(np.arctan2(pts[sel, 1] - c[1], pts[sel, 0] - c[0])) % 360.0
    return len(set((ang // 10).astype(int))) / 36.0


def _label_radii(dims) -> list[float]:
    """Radii the drawing states as numbers: R labels, diameters / 2, thread diameters / 2."""
    out = set()
    for d in dims:
        if not d.value:
            continue
        if d.dim_type == "promien":
            out.add(d.value)
        elif d.dim_type in ("srednica", "gwint"):
            out.add(d.value / 2.0)
    return sorted(out)


def _evaluate(view: DrawingView, proj: ModelProjection, rot: int, scale: float, threads,
              label_radii: list[float] | None = None) -> _Eval | None:
    R = _ROT[rot]
    mpts = proj.pts @ R.T
    axis = 0 if view.break_axis == "x" else 1
    perp = 1 - axis

    prims = [_to_mm(p, scale) for p in view.required_prims]
    opt = _to_mm(view.optional, scale) if len(view.optional) else np.zeros((0, 2))
    zones = []
    for a, b in view.break_zones():
        v0, v1 = a * PT_TO_MM / scale, b * PT_TO_MM / scale
        if axis == 1:
            v0, v1 = -v0, -v1
        zones.append((min(v0, v1), max(v0, v1)))

    def outside_zones(pts: np.ndarray) -> np.ndarray:
        keep = np.ones(len(pts), bool)
        for lo, hi in zones:
            keep &= ~((pts[:, axis] >= lo) & (pts[:, axis] <= hi))
        return keep

    prim_keep = [outside_zones(p) for p in prims]
    d_pts = np.vstack([p[k] for p, k in zip(prims, prim_keep)])
    if len(d_pts) == 0:
        return None
    opt = opt[outside_zones(opt)] if len(opt) else opt
    db, mb = _bbox_of(d_pts), _bbox_of(mpts)

    piece = np.zeros(len(d_pts), int)  # 0 = first, 1 = middle, 2 = last
    if zones:
        piece[:] = 1
        piece[d_pts[:, axis] <= zones[0][0]] = 0
        piece[d_pts[:, axis] >= zones[-1][1]] = 2

    shift = ((db[:2] + db[2:]) - (mb[:2] + mb[2:])) / 2.0
    mtree = cKDTree(mpts)
    if not zones:
        for _ in range(4):  # robust refinement: median of nearest-point offsets
            dist, idx = mtree.query(d_pts - shift)
            near = dist < 2.0
            if near.sum() < 10:
                break
            delta = np.median(d_pts[near] - (mpts[idx[near]] + shift), axis=0)
            shift = shift + delta
            if np.linalg.norm(delta) < 1e-4:
                break
        shifts = {0: shift}
    else:
        s0, s2 = shift.copy(), shift.copy()
        s0[axis] = d_pts[piece == 0, axis].min() - mb[axis] if (piece == 0).any() else shift[axis]
        s2[axis] = d_pts[piece == 2, axis].max() - mb[axis + 2] if (piece == 2).any() else shift[axis]
        shifts = {0: s0, 2: s2}

    d_bad = np.zeros(len(d_pts), bool)
    m_bad = np.zeros(len(mpts), bool)
    m_checked = np.zeros(len(mpts), bool)
    m_world = mpts + shifts[0]
    ref_all = np.vstack([d_pts, opt]) if len(opt) else d_pts
    ref_tree = cKDTree(ref_all)

    for pc, sh in shifts.items():
        sel = piece == pc
        if not sel.any():
            continue
        mp = mpts + sh
        dist, _ = mtree.query(d_pts[sel] - sh)
        d_bad[sel] = dist > TOL_MM
        if zones:
            inside = mp[:, axis] <= zones[0][0] if pc == 0 else mp[:, axis] >= zones[-1][1]
        else:
            inside = np.ones(len(mp), bool)
        if inside.any():
            md, _ = ref_tree.query(mp[inside])
            idx = np.where(inside)[0]
            m_bad[idx] = md > TOL_MM
            m_checked[idx] = True
            m_world[idx] = mp[idx]

    hidden_bad = 0
    mid_shift = None
    through_lines: list[float] = []
    if zones:
        # model between the end pieces: the drawing asserts a constant section there
        between = ((mpts + shifts[0])[:, axis] > zones[0][0]) & ((mpts + shifts[2])[:, axis] < zones[-1][1])
        if between.any():
            seg = mpts[between]
            span = seg[:, axis].max() - seg[:, axis].min()
            keys = np.round(seg[:, perp] / 0.05).astype(int)
            bad_mask = np.ones(len(seg), bool)
            # drawn outline lines of the end pieces (only primitives running along the break axis)
            along = []
            for p_mm, k in zip(prims, prim_keep):
                pk = p_mm[k]
                if len(pk) >= 2 and np.ptp(pk[:, perp]) < 0.05 and np.ptp(pk[:, axis]) > 1.0:
                    along.append(pk[:, perp])
            end_perp = (np.concatenate(along) - shifts[0][perp]) if along else np.zeros(0)
            for k in np.unique(keys):
                g = keys == k
                runs_through = seg[g, axis].max() - seg[g, axis].min() >= 0.9 * span
                drawn = len(end_perp) and np.any(np.abs(end_perp - seg[g, perp].mean()) <= TOL_MM)
                if runs_through and drawn:
                    bad_mask[g] = False  # a line through the hidden region, continuing a drawn line
                    through_lines.append(float(seg[g, perp].mean()))
            idx = np.where(between)[0]
            m_checked[idx] = True
            m_bad[idx[bad_mask]] = True
            hidden_bad = int(bad_mask.sum())

        mid = piece == 1
        if mid.any():  # middle pieces are not to scale along the axis: best shift anywhere
            mid_pts = d_pts[mid]
            best_bad, best_sh = None, None
            span = mb[axis + 2] - mb[axis]
            base = shifts[0].copy()
            def try_shift(s_ax):
                nonlocal best_bad, best_sh
                sh = base.copy()
                sh[axis] = mid_pts[:, axis].min() - mb[axis] + s_ax
                bad = mtree.query(mid_pts - sh)[0] > TOL_MM
                if best_bad is None or bad.sum() < best_bad.sum():
                    best_bad, best_sh = bad, sh
                return s_ax, int(bad.sum())

            coarse = [try_shift(s_ax) for s_ax in np.arange(-span - 5, span + 5, 1.0)]
            for s0, _ in sorted(coarse, key=lambda t: t[1])[:3]:  # refine around the best coarse shifts
                for s_ax in np.arange(s0 - 1.0, s0 + 1.0, 0.05):
                    try_shift(s_ax)
                    if not best_bad.any():
                        break
                if not best_bad.any():
                    break
            d_bad[mid] = best_bad
            mid_shift = best_sh

    # thread conventions (drawn lines with no solid geometry), only where the model has the thread
    rings: list = []
    if threads:
        centres = _arc_centres(prims, prim_keep) if not zones else []
        for r_nom, r_min in threads:
            for c, r_fit in centres:
                if not (r_min - TOL_MM <= r_fit <= r_nom + TOL_MM):
                    continue
                if _circle_coverage(m_world, c, r_min - TOL_MM, r_nom + TOL_MM) < 0.75:
                    continue  # no hole / shaft of this thread in the model here
                rings.append((c, r_min - TOL_MM, r_nom + TOL_MM))
                rd = np.hypot(d_pts[:, 0] - c[0], d_pts[:, 1] - c[1])
                d_bad &= ~((rd >= r_min - TOL_MM) & (rd <= r_nom + TOL_MM))
                rm = np.hypot(m_world[:, 0] - c[0], m_world[:, 1] - c[1])
                m_bad &= ~((rm >= r_min - TOL_MM) & (rm <= r_nom + TOL_MM))
        # thread limit line: straight, as long as a thread diameter, both ends on model edges
        start = 0
        for p, k in zip(prims, prim_keep):
            n = int(k.sum())
            seg = slice(start, start + n)
            start += n
            if n == 0 or not k.all() or not d_bad[seg].any():
                continue
            length = float(np.linalg.norm(p[-1] - p[0]))
            if not _is_straight(p) or not any(abs(length - 2 * r_nom) <= 2 * TOL_MM for r_nom, _ in threads):
                continue
            pc = int(piece[seg][0])
            sh = mid_shift if pc == 1 else shifts.get(pc, shifts[0])
            if sh is None:
                continue
            if all(mtree.query(e - sh)[0] <= TOL_MM for e in (p[0], p[-1])):
                d_bad[seg] = False

    arc_errors: list[str] = []
    if not zones:
        mt = cKDTree(m_world)
        for p_mm, k in zip(prims, prim_keep):
            if len(p_mm) < 8 or not k.all() or _is_straight(p_mm):
                continue
            fit = _fit_circle(p_mm)
            if fit is None:
                continue
            c, r, sweep = fit
            if sweep < 20.0 or r > 500:
                continue
            if any(np.linalg.norm(c - rc) < TOL_MM and lo <= r <= hi for rc, lo, hi in rings):
                continue
            idx = sorted({i for lst in mt.query_ball_point(p_mm, 1.5 * TOL_MM) for i in lst})
            if len(idx) < 8:
                continue
            q = m_world[idx]
            a_d = np.degrees(np.arctan2(p_mm[:, 1] - c[1], p_mm[:, 0] - c[0]))
            a_m = np.degrees(np.arctan2(q[:, 1] - c[1], q[:, 0] - c[0]))
            lo_a, hi_a = _angle_span(a_d)
            q = q[_in_span(a_m, lo_a, hi_a)]
            mfit = _fit_circle(q) if len(q) >= 8 else None
            if mfit is None:
                continue
            centre_off = float(np.linalg.norm(mfit[0] - c))
            # a drawn arc within 3 % of a radius stated on the drawing must have exactly that radius
            stated = [lr for lr in (label_radii or []) if abs(lr - r) <= max(0.03 * lr, ARC_R_TOL)]
            if stated:
                target = min(stated, key=lambda lr: abs(lr - r))
                r_bad = abs(mfit[1] - target) > max(0.01, 0.002 * target)
            else:
                r_bad = abs(mfit[1] - r) > max(ARC_R_TOL, 0.002 * r)
            if r_bad or (r >= 2.0 and sweep >= 90 and centre_off > ARC_C_TOL):
                arc_errors.append(
                    f"łuk R{r:.2f} na rysunku, w modelu R{mfit[1]:.2f} (środek przesunięty o {centre_off:.3f} mm) "
                    f"(środek x {c[0] - db[0]:.1f}, y {c[1] - db[1]:.1f} mm od lewego dolnego rogu widoku)"
                )

    m_bad &= m_checked
    score = d_bad.sum() / len(d_pts) + m_bad.sum() / max(1, m_checked.sum()) + 0.01 * len(arc_errors)
    hid = _to_mm(view.hidden, scale) if len(view.hidden) else np.zeros((0, 2))
    return _Eval(float(score), proj.name, rot, scale, d_pts, d_bad, m_world, m_bad, hidden_bad,
                 shifts=shifts, zones=zones, axis=axis, ref_tree=ref_tree, rings=rings,
                 hidden_tree=cKDTree(hid) if len(hid) else None, through_lines=through_lines,
                 arc_errors=arc_errors)


# ---------------------------------------------------------------------------
# Whole part: one scale, one orientation, projection conventions
# ---------------------------------------------------------------------------


@dataclass
class ViewResult:
    view_index: int
    size_mm: tuple[float, float]
    ok: bool
    uncertain: bool = False
    kind: str = "rzut"
    projection: str = ""
    scale: float | None = None
    unmatched_drawing: list[str] = field(default_factory=list)
    unmatched_model: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    frame: tuple | None = None  # (towards viewer, sheet right, sheet up) model directions

    def message(self) -> str:
        if self.view_index == 0:
            return "Cały model: " + "; ".join(self.notes)
        head = (
            f"Widok {self.view_index} ({self.size_mm[0]:.1f} x {self.size_mm[1]:.1f} mm, "
            f"skala {self.scale:g}, rzut modelu {self.projection})"
            if self.scale
            else f"Widok {self.view_index}"
        )
        parts = self.unmatched_drawing + self.unmatched_model + self.notes
        return head + (": " + "; ".join(parts) if parts else "")


def _describe(points: np.ndarray, origin: np.ndarray, label: str, limit: int = 4) -> list[str]:
    """Cluster bad points into a few readable locations (mm from the view's lower-left corner)."""
    if len(points) == 0:
        return []
    rel = points - origin
    tree = cKDTree(rel)
    seen = np.zeros(len(rel), bool)
    groups = []
    for i in range(len(rel)):
        if seen[i]:
            continue
        grp, frontier = {i}, [i]
        while frontier:
            j = frontier.pop()
            for k in tree.query_ball_point(rel[j], 2.0):
                if k not in grp:
                    grp.add(k)
                    frontier.append(k)
        g = np.fromiter(grp, int)
        seen[g] = True
        x0, y0 = rel[g].min(axis=0)
        x1, y1 = rel[g].max(axis=0)
        groups.append((len(g), f"{label} x {x0:.1f}..{x1:.1f}, y {y0:.1f}..{y1:.1f} mm"))
    groups.sort(key=lambda t: -t[0])
    return [s for _, s in groups[:limit]]


def model_edge_points(shape, step: float = 0.25) -> np.ndarray:
    """3D sample points on the sharp edges of the solid (no seams, no tangent edges)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepLib import BRepLib
    from OCP.GCPnts import GCPnts_UniformAbscissa
    from OCP.GeomAbs import GeomAbs_C0
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    shape = BRepBuilderAPI_Copy(shape).Shape()
    BRepLib.EncodeRegularity_s(shape, 1e-3)
    emap = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, emap)
    out: list[np.ndarray] = []
    for i in range(1, emap.Extent() + 1):
        edge = TopoDS.Edge_s(emap.FindKey(i))
        if BRep_Tool.Degenerated_s(edge):
            continue
        faces = [TopoDS.Face_s(f) for f in emap.FindFromIndex(i)]
        if len(faces) == 1 and BRep_Tool.IsClosed_s(edge, faces[0]):
            continue  # seam of a cylinder / cone
        if len(faces) == 2 and faces[0].IsSame(faces[1]):
            continue  # seam listed twice
        if len(faces) == 2 and BRep_Tool.Continuity_s(edge, faces[0], faces[1]) != GeomAbs_C0:
            continue  # tangent edge: not drawn
        curve = BRepAdaptor_Curve(edge)
        u0, u1 = curve.FirstParameter(), curve.LastParameter()
        ua = GCPnts_UniformAbscissa(curve, step, u0, u1)
        params = [ua.Parameter(k) for k in range(1, ua.NbPoints() + 1)] if ua.IsDone() else list(np.linspace(u0, u1, 5))
        out.append(np.asarray([(curve.Value(t).X(), curve.Value(t).Y(), curve.Value(t).Z()) for t in params]))
    return np.vstack(out) if out else np.zeros((0, 3))


def tangent_only_faces(shape, grid: int = 9) -> list[tuple[str, np.ndarray, dict[str, np.ndarray] | None, tuple | None]]:
    """Curved faces bounded only by tangent edges or seams (fillets meeting both neighbours
    smoothly), with sample points inside each face. Such faces draw no line of their own:
    a drawing shows them only where it looks at them edge-on (as an arc).
    The third item: for a toroidal face its silhouette meridians per view direction (a view
    missing from the dict does not show the face edge-on); None for any other face. The fourth:
    the torus (centre, axis, major radius, minor radius), None for any other face."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.BRepLib import BRepLib
    from OCP.GeomAbs import GeomAbs_C0, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Torus
    from OCP.gp import gp_Pnt2d
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_IN
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    shape = BRepBuilderAPI_Copy(shape).Shape()
    BRepLib.EncodeRegularity_s(shape, 1e-3)
    emap = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, emap)
    out = []
    fexp = TopExp_Explorer(shape, TopAbs_FACE)
    while fexp.More():
        face = TopoDS.Face_s(fexp.Current())
        fexp.Next()
        surf = BRepAdaptor_Surface(face, True)
        st = surf.GetType()
        if st == GeomAbs_Plane:
            continue
        smooth = True
        eexp = TopExp_Explorer(face, TopAbs_EDGE)
        while eexp.More() and smooth:
            edge = TopoDS.Edge_s(eexp.Current())
            eexp.Next()
            if BRep_Tool.Degenerated_s(edge) or BRep_Tool.IsClosed_s(edge, face):
                continue
            faces = [TopoDS.Face_s(f) for f in emap.FindFromKey(edge)]
            other = [f for f in faces if not f.IsSame(face)]
            if not other or BRep_Tool.Continuity_s(edge, face, other[0]) == GeomAbs_C0:
                smooth = False
        if not smooth:
            continue
        u0, u1, v0, v1 = surf.FirstUParameter(), surf.LastUParameter(), surf.FirstVParameter(), surf.LastVParameter()
        clf = BRepClass_FaceClassifier()
        pts = []
        for i in range(1, grid):
            for j in range(1, grid):
                u, v = u0 + (u1 - u0) * i / grid, v0 + (v1 - v0) * j / grid
                clf.Perform(face, gp_Pnt2d(u, v), 1e-6)
                if clf.State() == TopAbs_IN:
                    q = surf.Value(u, v)
                    pts.append((q.X(), q.Y(), q.Z()))
        if pts:
            profiles: dict[str, np.ndarray] | None = None
            torus = None
            if st == GeomAbs_Cylinder:
                label = f"R{surf.Cylinder().Radius():g}"
            elif st == GeomAbs_Torus:
                label = f"R{surf.Torus().MinorRadius():g}"
                profiles = _torus_profiles(face, surf, clf, grid)
                t = surf.Torus()
                loc, ax = t.Location(), t.Axis().Direction()
                torus = (np.array([loc.X(), loc.Y(), loc.Z()]), np.array([ax.X(), ax.Y(), ax.Z()]),
                         t.MajorRadius(), t.MinorRadius())
            else:
                label = "powierzchnia krzywoliniowa"
            out.append((label, np.asarray(pts), profiles, torus))
    return out


def _torus_profiles(face, surf, clf, grid: int) -> dict[str, np.ndarray]:
    """Points of a toroidal face (a rounded shaft step) on its silhouette meridians, per view
    direction. A view looking across the torus axis shows the face only as its profile arc
    (where the face turns edge-on); interior points of the face project inside the outline and
    lie on no line, so they cannot tell whether the view shows the rounding. Only meridians
    that really belong to the face (classified inside it) are returned, and only when the
    face holds a real part of the meridian (at least TORUS_MIN_MERIDIAN_PTS of its samples):
    a meridian touching a trimmed face only at its border is no profile of the face."""
    from OCP.gp import gp_Pnt2d
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON

    ax = surf.Torus().Position()
    zd, xd, yd = ax.Direction(), ax.XDirection(), ax.YDirection()
    z = np.array([zd.X(), zd.Y(), zd.Z()])
    xv = np.array([xd.X(), xd.Y(), xd.Z()])
    yv = np.array([yd.X(), yd.Y(), yd.Z()])
    u0, u1, v0, v1 = surf.FirstUParameter(), surf.LastUParameter(), surf.FirstVParameter(), surf.LastVParameter()
    out: dict[str, np.ndarray] = {}
    for name, (n, _x) in VIEW_DIRS.items():
        nv = np.array(n, float)
        if abs(float(nv @ z)) > 1e-6:
            continue  # the view does not look across the axis: no profile arc
        side = np.cross(z, nv)
        pts = []
        for d in (side, -side):
            u = math.atan2(float(d @ yv), float(d @ xv))
            u = next((uu for uu in (u, u + 2 * math.pi, u - 2 * math.pi, u + 4 * math.pi) if u0 - 1e-9 <= uu <= u1 + 1e-9), None)
            if u is None:
                continue
            meridian = []
            for j in range(1, grid):
                v = v0 + (v1 - v0) * j / grid
                clf.Perform(face, gp_Pnt2d(u, v), 1e-6)
                if clf.State() in (TopAbs_IN, TopAbs_ON):
                    q = surf.Value(u, v)
                    meridian.append((q.X(), q.Y(), q.Z()))
            if len(meridian) >= max(TORUS_MIN_MERIDIAN_PTS, (grid - 1) // 2):
                pts.extend(meridian)
        if pts:
            out[name] = np.asarray(pts)
    return out


class _VisibleSilhouette:
    """Which silhouette points of a toroidal face are visible in a view direction: nothing of
    the part lies in front of them. A silhouette hidden behind another part of the model, or
    inside the material, draws no line, even where its projection falls on some other drawn
    line (e.g. an equal rounding of a rib in front of it). Rays are cast towards the viewer from
    the point and from points TOL_MM / 2 beside it in the view plane, so a silhouette that only
    grazes another surface within the drawing tolerance does not count as visible either."""

    def __init__(self, shape):
        from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

        self._inter = IntCurvesFace_ShapeIntersector()
        self._inter.Load(shape, 1e-6)

    def _blocked(self, p: np.ndarray, n: np.ndarray, torus) -> bool:
        from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

        c, a, big_r, small_r = torus
        self._inter.Perform(gp_Lin(gp_Pnt(*map(float, p)), gp_Dir(*map(float, n))), 1e-4, 1e9)
        if not self._inter.IsDone():
            return True
        for i in range(1, self._inter.NbPnt() + 1):
            q = self._inter.Pnt(i)
            d = np.array([q.X(), q.Y(), q.Z()]) - c
            h = float(d @ a)
            rho = float(np.linalg.norm(d - h * a))
            if abs(math.hypot(rho - big_r, h) - small_r) > 1e-4:
                return True  # a face other than the rounding itself lies in front
        return False

    def __call__(self, name: str, pts3: np.ndarray, torus) -> np.ndarray:
        n, x = VIEW_DIRS[name]
        n_v, x_v = np.array(n, float), np.array(x, float)
        y_v = np.cross(n_v, x_v)
        eps = TOL_MM / 2
        offsets = [np.zeros(3), eps * x_v, -eps * x_v, eps * y_v, -eps * y_v]
        return np.array([not any(self._blocked(p + o, n_v, torus) for o in offsets) for p in pts3], bool)


def _explained(ev: _Eval, pts3: np.ndarray, conventions: bool = True) -> np.ndarray:
    """Which 3D points lie on a drawn line (or thread convention) of this view.
    conventions=False: only visible drawn lines count (no hidden lines, no thread rings)."""
    n, x = VIEW_DIRS[ev.proj]
    n_v, x_v = np.array(n, float), np.array(x, float)
    y_v = np.cross(n_v, x_v)
    uv = np.column_stack([pts3 @ x_v, pts3 @ y_v]) @ _ROT[ev.rot].T
    ok = np.zeros(len(pts3), bool)

    def on_lines(w: np.ndarray) -> np.ndarray:
        hit = ev.ref_tree.query(w)[0] <= TOL_MM
        if conventions and ev.hidden_tree is not None:
            hit |= ev.hidden_tree.query(w)[0] <= TOL_MM
        return hit

    if not ev.zones:
        w = uv + ev.shifts[0]
        ok |= on_lines(w)
        for c, lo, hi in (ev.rings if conventions else []):
            r = np.hypot(w[:, 0] - c[0], w[:, 1] - c[1])
            ok |= (r >= lo) & (r <= hi)
        return ok
    a, pp = ev.axis, 1 - ev.axis
    w0, w2 = uv + ev.shifts[0], uv + ev.shifts[2]
    first = w0[:, a] <= ev.zones[0][0]
    last = w2[:, a] >= ev.zones[-1][1]
    ok[first] = on_lines(w0[first])
    ok[last] = on_lines(w2[last])
    between = ~(first | last)  # hidden by the break: only lines continuing through it
    if between.any() and ev.through_lines:
        perp_vals = uv[between, pp]
        ok[between] = np.min(np.abs(perp_vals[:, None] - np.array(ev.through_lines)[None, :]), axis=1) <= TOL_MM
    return ok


def _neighbours(views: list[DrawingView]) -> list[tuple[int, int, str]]:
    """(i, j, 'h' | 'v'): j right of i ('h') or j below i ('v') in a projection row/column."""
    out = []
    boxes = [v.core_bbox() for v in views]
    def y_overlap(a, b):
        return min(a[3], b[3]) - max(a[1], b[1]) >= 0.5 * min(a[3] - a[1], b[3] - b[1])

    def x_overlap(a, b):
        return min(a[2], b[2]) - max(a[0], b[0]) >= 0.5 * min(a[2] - a[0], b[2] - b[0])

    for i, j in itertools.permutations(range(len(views)), 2):
        a, b = boxes[i], boxes[j]
        others = [boxes[k] for k in range(len(views)) if k not in (i, j)]
        if b[0] >= a[2] and y_overlap(a, b):
            if not any(o[0] >= a[2] - 1 and o[2] <= b[0] + 1 and y_overlap(a, o) for o in others):
                out.append((i, j, "h"))
        elif b[1] >= a[3] and x_overlap(a, b):
            if not any(o[1] >= a[3] - 1 and o[3] <= b[1] + 1 and x_overlap(a, o) for o in others):
                out.append((i, j, "v"))
    return out


def _consistent(frames, pairs, convention: str) -> bool:
    for i, j, kind in pairs:
        if frames[i] is None or frames[j] is None:
            continue
        n_i, A_i, B_i = frames[i]
        n_j, A_j, B_j = frames[j]
        if kind == "h":
            want_n = tuple(-v for v in A_i) if convention == "europejski" else A_i
            if B_i != B_j or n_j != want_n:
                return False
        else:
            want_n = B_i if convention == "europejski" else tuple(-v for v in B_i)
            if A_i != A_j or n_j != want_n:
                return False
    return True


def compare_views(shape, views: list[DrawingView], dims, sheet_scale: float | None) -> list[ViewResult]:
    """Match all drawing views with ONE model orientation and scale; report unmatched geometry."""
    if not views:
        return []
    projections = project_model(shape)
    threads = _thread_specs(dims)
    radii = _label_radii(dims)

    # 1. scale: the sheet's, or one standard scale for the whole part
    if sheet_scale:
        scales = [sheet_scale]
    else:
        cand_scales: set[float] = set()
        for view in views:
            bb = view.core_bbox()
            for proj in projections:
                for rot in range(4):
                    mb = _bbox_of(proj.pts @ _ROT[rot].T)
                    ests = []
                    if view.break_axis != "x" and mb[2] - mb[0] > 1e-6:
                        ests.append((bb[2] - bb[0]) * PT_TO_MM / (mb[2] - mb[0]))
                    if view.break_axis != "y" and mb[3] - mb[1] > 1e-6:
                        ests.append((bb[3] - bb[1]) * PT_TO_MM / (mb[3] - mb[1]))
                    if ests and (len(ests) == 1 or abs(ests[0] / ests[1] - 1) < 0.02):
                        est = float(np.mean(ests))
                        cand_scales.add(min(STANDARD_SCALES, key=lambda s: abs(math.log(s / max(est, 1e-9)))))
        scales = sorted(cand_scales) or [1.0]

    evals: dict[float, list[list[_Eval]]] = {}
    for sc in scales:
        per_view = []
        for view in views:
            cands = [ev for proj in projections for rot in range(4)
                     if (ev := _evaluate(view, proj, rot, sc, threads, radii)) is not None]
            cands.sort(key=lambda e: e.score)
            per_view.append(cands)
        evals[sc] = per_view
    scale = min(scales, key=lambda s: sum(c[0].score if c else 1.0 for c in evals[s]))
    per_view = evals[scale]

    # 2. orientation: one assignment explaining every orthographic view, by projection rules
    pairs = [p for p in _neighbours(views) if views[p[0]].kind == "rzut" and views[p[1]].kind == "rzut"]
    options = []
    for cands in per_view:
        if not cands:
            options.append([None])
            continue
        best = cands[0].score
        tied = [c for c in cands if c.score <= best + 1e-12]
        options.append(tied + [c for c in cands[len(tied):len(tied) + 6]])
    best_by_conv: dict[str, tuple] = {}
    for combo in itertools.product(*options):
        frames = [c.frame() if c is not None else None for c in combo]
        total = sum(c.score for c in combo if c is not None)
        for conv in ("europejski", "amerykanski"):
            if _consistent(frames, pairs, conv) and (conv not in best_by_conv or total < best_by_conv[conv][0] - 1e-12):
                best_by_conv[conv] = (total, combo, conv)
    # ISO / PN drawings use first-angle projection; third-angle only is reported, not trusted
    def all_ok(entry) -> bool:
        return entry is not None and all(c is None or c.ok for c in entry[1])

    eu, us = best_by_conv.get("europejski"), best_by_conv.get("amerykanski")
    american_only = bool(pairs) and us is not None and all_ok(us) and not all_ok(eu)
    chosen = us if american_only else (eu or us)
    layout_note = None
    if chosen is None:
        combo = tuple(c[0] if c else None for c in per_view)
        layout_note = (
            "widoki nie dają się ułożyć w jednym rzucie prostokątnym (europejskim ani amerykańskim) "
            "przy jednej orientacji modelu: możliwe odbicie lustrzane albo widoki przestawione"
        )
    else:
        combo = chosen[1]

    # every sharp model edge must appear in at least one drawn view (as a visible line, a
    # line it coincides with, or a thread convention); otherwise it is not on the drawing
    unexplained_note = None
    any_special = any(v.kind != "rzut" for v in views)
    if all(ev is not None for ev in combo):
        pts3 = model_edge_points(shape)
        if len(pts3):
            explained = np.zeros(len(pts3), bool)
            for ev in combo:
                explained |= _explained(ev, pts3)
            bad = pts3[~explained]
            if len(bad) > MAX_UNMATCHED_POINTS:
                lo, hi = bad.min(axis=0), bad.max(axis=0)
                unexplained_note = (
                    f"{len(bad)} punktów krawędzi modelu nie ma odpowiednika w żadnym widoku rysunku "
                    f"(obszar modelu x {lo[0]:.1f}..{hi[0]:.1f}, y {lo[1]:.1f}..{hi[1]:.1f}, "
                    f"z {lo[2]:.1f}..{hi[2]:.1f} mm): element niewidoczny na rysunku, np. otwór nieprzelotowy, "
                    "wnęka albo element na niewidocznej stronie"
                )

    # rounded edges that no view shows edge-on are not defined by the drawing
    hidden_round_note = None
    if all(ev is not None for ev in combo):
        unseen = []
        visible = _VisibleSilhouette(shape)
        for label, pts, profiles, torus in tangent_only_faces(shape):
            if profiles is None:
                frac = max(float(_explained(ev, pts).mean()) for ev in combo)
            else:
                # a toroidal rounding is seen edge-on only along its silhouette meridians, and only
                # where they are visible in that view (not hidden behind or inside the part); a view
                # not looking across the torus axis does not show its profile at all
                frac = max(float((_explained(ev, profiles[ev.proj], conventions=False)
                                  & visible(ev.proj, profiles[ev.proj], torus)).mean())
                           if ev.proj in profiles else 0.0
                           for ev in combo)
            if frac < 0.8:
                c = pts.mean(axis=0)
                unseen.append(f"{label} przy ({c[0]:.1f}, {c[1]:.1f}, {c[2]:.1f})")
        if unseen:
            hidden_round_note = (
                f"zaokrąglenia, których żaden widok nie pokazuje z profilu ({len(unseen)} powierzchni, np. "
                f"{'; '.join(unseen[:3])}): rysunek nie rozstrzyga, czy te krawędzie są zaokrąglone"
            )

    results: list[ViewResult] = []
    if hidden_round_note:
        results.append(ViewResult(0, (0.0, 0.0), False, uncertain=True, notes=[hidden_round_note]))
    if unexplained_note:
        results.append(ViewResult(0, (0.0, 0.0), False, uncertain=any_special, notes=[unexplained_note]))
    for vi, (view, ev) in enumerate(zip(views, combo)):
        bb = view.core_bbox()
        size_pt = ((bb[2] - bb[0]) * PT_TO_MM, (bb[3] - bb[1]) * PT_TO_MM)
        if ev is None:
            results.append(ViewResult(vi + 1, size_pt, False, uncertain=True, kind=view.kind,
                                      notes=["Brak linii widocznych w widoku."]))
            continue
        r = ViewResult(
            vi + 1,
            (size_pt[0] / ev.scale, size_pt[1] / ev.scale),
            ev.ok and layout_note is None,
            kind=view.kind,
            projection=f"{ev.proj}, obrót {ev.rot * 90}°" + (f", rzut {chosen[2]}" if chosen and pairs else ""),
            scale=ev.scale,
        )
        if not ev.ok:
            origin = _bbox_of(ev.d_pts)[:2]
            r.unmatched_drawing = _describe(ev.d_pts[ev.d_bad], origin, "linia rysunku bez krawędzi w modelu:")
            r.unmatched_model = _describe(ev.m_pts[ev.m_bad], origin, "krawędź modelu, której nie ma na rysunku:")
            if ev.hidden_bad > MAX_UNMATCHED_POINTS:
                r.notes.append("model ma krawędzie w części ukrytej przez przerwanie widoku "
                               "(przerwanie oznacza stały przekrój na tym odcinku)")
            if view.kind != "rzut":
                r.uncertain = True
                r.notes.append(f"widok typu '{view.kind}': niezgodność oznacza NIEPEWNE, nie BŁĄD")
        r.frame = ev.frame()
        if layout_note:  # ISO 128 drawings use first- or third-angle projection: this is an error
            r.notes.append(layout_note)
        elif american_only:
            r.uncertain = True
            r.ok = False
            r.notes.append("widoki pasują tylko w rzucie amerykańskim; rysunki PN/ISO stosują rzut europejski "
                           "(możliwe odbicie lustrzane modelu)")
        if ev.arc_errors:
            r.notes.extend(ev.arc_errors[:4])
        if view.is_broken:
            r.notes.append("widok przerwany: środkowe części porównane bez skali wzdłuż osi przerwania")
        results.append(r)
    return results
