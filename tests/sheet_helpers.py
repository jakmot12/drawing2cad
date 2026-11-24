"""Draw synthetic vector drawing sheets from a known-good model (test helper)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pymupdf

from drawing2cad.drawing_compare import PT_TO_MM, project_model_edges, view_frame

_ROT = {0: np.eye(2), 1: np.array([[0, -1], [1, 0]], float), 2: -np.eye(2), 3: np.array([[0, 1], [-1, 0]], float)}

# first-angle (European) frames for a front view looking along +Y with sheet up = +Z
FRONT = ("-Y", 3)
LEFT_VIEW_PLACED_RIGHT = ("-X", 0)
RIGHT_VIEW_PLACED_LEFT = ("+X", 0)
TOP_VIEW_PLACED_BELOW = ("+Z", 0)

# Inventor's line widths [pt]: visible edges, thread lines, thin lines (dimensions, axes)
VISIBLE_W = 1.44
THIN_W = 0.72


def draw_sheet(solid, path: Path, views: list[tuple[tuple[str, int], tuple[float, float]]],
               visible_w: float = 1.44, frame: str | None = None, frame_w: float = 1.98,
               scale: float = 1.0, annotate: bool = False, title_block: bool = False,
               hidden: bool = False) -> None:
    """views: [((projection, rotation), (x, y) of the model origin on the sheet [pt])].
    frame: None, 'rect' (one path) or 'lines' (four separate lines).
    scale: drawing scale (2.0 = 2:1).
    annotate: thin dimension lines with arrows and values, centre lines of circles and axes,
    drawn like Inventor does (solid thin segments, filled arrowheads), plus dimension texts.
    title_block: a title block in the lower right corner, attached to the frame.
    hidden: also draw hidden edges as thin dashed lines."""
    edges = project_model_edges(solid.val().wrapped)
    hidden_edges = _hidden_edges(solid.val().wrapped) if hidden else {}
    k = scale / PT_TO_MM
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for (name, rot), (ox, oy) in views:
        polys = []
        for e in edges[name]:
            ab = e @ _ROT[rot].T
            pts = [(ox + a * k, oy - b * k) for a, b in ab]
            polys.append(np.asarray(pts))
            page.draw_polyline(pts, color=(0, 0, 0), width=visible_w)
        for e in hidden_edges.get(name, []):
            ab = e @ _ROT[rot].T
            page.draw_polyline([(ox + a * k, oy - b * k) for a, b in ab], color=(0, 0, 0), width=visible_w / 2,
                               dashes="[3 1.5] 0")
        if annotate and polys:
            _annotate_view(page, polys, scale, visible_w / 2)
    for y in (100, 110, 120):  # thin lines: a second line width on the page
        page.draw_line((40, y), (80, y), color=(0, 0, 0), width=visible_w / 2)
    if frame == "rect":
        page.draw_rect(pymupdf.Rect(28, 28, 567, 814), color=(0, 0, 0), width=frame_w)
    elif frame == "lines":
        for a, b in (((28, 28), (567, 28)), ((567, 28), (567, 814)), ((567, 814), (28, 814)), ((28, 814), (28, 28))):
            page.draw_line(a, b, color=(0, 0, 0), width=frame_w)
    if title_block:
        x0, y0, x1, y1 = 367.0, 744.0, 567.0, 814.0
        for a, b in (((x0, y0), (x1, y0)), ((x0, y0), (x0, y1)), ((x0, y0 + 35), (x1, y0 + 35)),
                     ((x0 + 100, y0 + 35), (x0 + 100, y1))):
            page.draw_line(a, b, color=(0, 0, 0), width=visible_w)
        page.insert_text((x0 + 5, y0 + 20), "Czesc testowa", fontsize=12)
        page.insert_text((x0 + 5, y0 + 55), f"Skala {_scale_text(scale)}", fontsize=9)
    doc.save(str(path))


def _hidden_edges(shape) -> dict[str, list[np.ndarray]]:
    """Hidden sharp edges of the solid seen along the 6 axis directions (HLR)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepLib import BRepLib
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.HLRAlgo import HLRAlgo_Projector
    from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape

    from drawing2cad.drawing_compare import MODEL_STEP_MM, VIEW_DIRS, _discretize_edges

    shape = BRepBuilderAPI_Copy(shape).Shape()
    BRepLib.EncodeRegularity_s(shape, 1e-3)
    out = {}
    for name, (n, x) in VIEW_DIRS.items():
        algo = HLRBRep_Algo()
        algo.Add(shape)
        algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*n), gp_Dir(*x))))
        algo.Update()
        algo.Hide()
        hlr = HLRBRep_HLRToShape(algo)
        out[name] = _discretize_edges(hlr.HCompound(), MODEL_STEP_MM) + _discretize_edges(hlr.OutLineHCompound(), MODEL_STEP_MM)
    return out


def _scale_text(scale: float) -> str:
    return f"{scale:g}:1" if scale >= 1 else f"1:{1 / scale:g}"


def _arrow(page, tip, direction, w):
    d = np.asarray(direction, float) / (np.linalg.norm(direction) or 1.0)
    n = np.array([-d[1], d[0]])
    tip = np.asarray(tip, float)
    base = tip - 7.0 * d
    pts = [tuple(tip), tuple(base + 1.2 * n), tuple(base - 1.2 * n), tuple(tip)]
    page.draw_polyline(pts, color=(0, 0, 0), fill=(0, 0, 0), width=0)


def _centre_line(page, a, b, w):
    """Dash-dot centre line written as separate solid thin segments (as Inventor does)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    L = float(np.linalg.norm(b - a))
    if L < 1e-6:
        return
    d = (b - a) / L
    pos = 0.0
    pattern = [(12.0, 3.0), (1.5, 3.0)]
    i = 0
    while pos < L:
        dash, gap = pattern[i % 2]
        end = min(pos + dash, L)
        page.draw_line(tuple(a + pos * d), tuple(a + end * d), color=(0, 0, 0), width=w)
        pos = end + gap
        i += 1


def _annotate_view(page, polys: list[np.ndarray], scale: float, w: float) -> None:
    pts = np.vstack(polys)
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    off = 22.0
    # overall width below the view
    yd = y1 + off
    page.draw_line((x0, y1 + 2.0), (x0, yd + 3.0), color=(0, 0, 0), width=w)
    page.draw_line((x1, y1 + 2.0), (x1, yd + 3.0), color=(0, 0, 0), width=w)
    page.draw_line((x0, yd), (x1, yd), color=(0, 0, 0), width=w)
    _arrow(page, (x0, yd), (-1, 0), w)
    _arrow(page, (x1, yd), (1, 0), w)
    page.insert_text(((x0 + x1) / 2 - 8, yd - 2), f"{(x1 - x0) * PT_TO_MM / scale:.4g}", fontsize=9)
    # overall height right of the view
    xd = x1 + off
    page.draw_line((x1 + 2.0, y0), (xd + 3.0, y0), color=(0, 0, 0), width=w)
    page.draw_line((x1 + 2.0, y1), (xd + 3.0, y1), color=(0, 0, 0), width=w)
    page.draw_line((xd, y0), (xd, y1), color=(0, 0, 0), width=w)
    _arrow(page, (xd, y0), (0, -1), w)
    _arrow(page, (xd, y1), (0, 1), w)
    page.insert_text((xd - 2, (y0 + y1) / 2 + 8), f"{(y1 - y0) * PT_TO_MM / scale:.4g}", fontsize=9, rotate=90)
    # centre marks of full circles
    for p in polys:
        if len(p) < 12 or np.linalg.norm(p[0] - p[-1]) > 0.05:
            continue
        c = p.mean(axis=0)
        r = np.hypot(*(p - c).T)
        if r.mean() < 1.0 or r.std() > 0.02 * r.mean():
            continue
        ext = r.mean() + 6.0
        _centre_line(page, (c[0] - ext, c[1]), (c[0] + ext, c[1]), w)
        _centre_line(page, (c[0], c[1] - ext), (c[0], c[1] + ext), w)


# ---------------------------------------------------------------------------
# First-angle (European) layout
# ---------------------------------------------------------------------------


def _cross(a, b):
    return tuple(int(v) for v in np.cross(np.array(a), np.array(b)))


def _neg(a):
    return tuple(-v for v in a)


def european_view(base: tuple[str, int], where: str) -> tuple[str, int]:
    """(projection, rotation) of the view placed `where` ('right', 'left', 'below', 'above')
    of the `base` view in first-angle (European) projection."""
    n, A, B = view_frame(*base)
    if where == "right":
        n2, B2 = _neg(A), B
        A2 = _cross(B2, n2)
    elif where == "left":
        A2, B2 = _neg(n), B
        n2 = _cross(A2, B2)
    elif where == "below":
        n2, A2 = B, A
        B2 = _cross(n2, A2)
    elif where == "above":
        A2, B2 = A, n
        n2 = _cross(A2, B2)
    else:
        raise ValueError(where)
    for name in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
        for rot in range(4):
            if view_frame(name, rot) == (n2, A2, B2):
                return name, rot
    raise AssertionError("no view frame")


def layout_views(solid, main: tuple[str, int], others: list[str], scale: float = 1.0, gap_pt: float = 70.0,
                 page: tuple[float, float] = (595.0, 842.0)) -> list[tuple[tuple[str, int], tuple[float, float]]]:
    """Place the main view and its first-angle neighbours ('right', 'below', 'left', 'above'),
    aligned along the projection lines, the whole group centred in the upper part of the page."""
    edges = project_model_edges(solid.val().wrapped)
    k = scale / PT_TO_MM

    def ab_box(view):
        name, rot = view
        ab = np.vstack(edges[name]) @ _ROT[rot].T
        return ab[:, 0].min(), ab[:, 1].min(), ab[:, 0].max(), ab[:, 1].max()

    amin, bmin, amax, bmax = ab_box(main)
    placed = [(main, (0.0, 0.0))]
    for where in others:
        v = european_view(main, where)
        a0, b0, a1, b1 = ab_box(v)
        if where == "right":
            pos = (amax * k + gap_pt - a0 * k, 0.0)
        elif where == "left":
            pos = (amin * k - gap_pt - a1 * k, 0.0)
        elif where == "below":
            pos = (0.0, -bmin * k + gap_pt + b1 * k)
        else:
            pos = (0.0, -bmax * k - gap_pt + b0 * k)
        placed.append((v, pos))
    # sheet bbox of the group, then centre it
    xs, ys = [], []
    for view, (ox, oy) in placed:
        a0, b0, a1, b1 = ab_box(view)
        xs += [ox + a0 * k, ox + a1 * k]
        ys += [oy - b1 * k, oy - b0 * k]
    cx = page[0] / 2 - (min(xs) + max(xs)) / 2
    cy = 330.0 - (min(ys) + max(ys)) / 2
    assert max(xs) - min(xs) < page[0] - 120 and max(ys) - min(ys) < 560, "views do not fit on the sheet"
    return [(view, (ox + cx, oy + cy)) for view, (ox, oy) in placed]

