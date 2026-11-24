"""STEP -> web preview conversion (binary STL triangles + B-Rep edge polylines as JSON)."""
from __future__ import annotations

import json
from pathlib import Path

import cadquery as cq

STL_TOLERANCE = 0.02  # mm, linear deflection of the triangulation
STL_ANGULAR_TOLERANCE = 0.1  # rad


def preview_paths(step_path: str | Path, preview_dir: str | Path) -> tuple[Path, Path]:
    stem = Path(step_path).stem
    d = Path(preview_dir)
    return d / f"{stem}.stl", d / f"{stem}.edges.json"


def _edge_polyline(edge: cq.Edge) -> list[list[float]]:
    if edge.geomType() == "LINE":
        ts = [0.0, 1.0]
    else:
        n = max(8, min(96, int(edge.Length() / 0.5) + 1))
        ts = [i / n for i in range(n + 1)]
    pts = edge.positions(ts)
    return [[round(p.x, 4), round(p.y, 4), round(p.z, 4)] for p in pts]


def export_preview(step_path: str | Path, preview_dir: str | Path) -> tuple[Path, Path]:
    """Convert a STEP file to `<stem>.stl` and `<stem>.edges.json` in `preview_dir` (cached)."""
    stl_path, edges_path = preview_paths(step_path, preview_dir)
    step_mtime = Path(step_path).stat().st_mtime
    if (
        stl_path.is_file()
        and edges_path.is_file()
        and stl_path.stat().st_mtime >= step_mtime
        and edges_path.stat().st_mtime >= step_mtime
    ):
        return stl_path, edges_path
    stl_path.parent.mkdir(parents=True, exist_ok=True)
    shape = cq.importers.importStep(str(step_path))
    cq.exporters.export(
        shape, str(stl_path), tolerance=STL_TOLERANCE, angularTolerance=STL_ANGULAR_TOLERANCE
    )
    edges = [_edge_polyline(e) for e in shape.edges().vals()]
    bb = shape.val().BoundingBox()
    payload = {
        "edges": edges,
        "bbox": [[bb.xmin, bb.ymin, bb.zmin], [bb.xmax, bb.ymax, bb.zmax]],
    }
    edges_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return stl_path, edges_path
