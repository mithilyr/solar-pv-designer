"""Read a panel layout from an AutoCAD DXF drawing.

Panels in a rooftop layout are usually drawn as copies of one block (INSERT) or
as closed rectangles. We find them, take their centre points and snap those to
a grid of columns (rail runs) and rows. In AutoCAD, save the layout with
SAVEAS → DXF before importing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .layout import EMPTY, PANEL, Grid, LayoutError


@dataclass
class DxfLayout:
    grid: Grid
    panel_count: int
    source: str
    panel_size: tuple[float, float]  # (across, along) in drawing units
    warnings: list[str] = field(default_factory=list)


def _rect_size(points) -> tuple[float, float] | None:
    pts = [(p[0], p[1]) for p in points]
    if len(pts) == 5 and abs(pts[0][0] - pts[-1][0]) < 1e-6 and abs(pts[0][1] - pts[-1][1]) < 1e-6:
        pts = pts[:4]
    if len(pts) != 4:
        return None
    xs, ys = sorted(p[0] for p in pts), sorted(p[1] for p in pts)
    # Axis-aligned rectangles only.
    if abs(xs[0] - xs[1]) > 1e-3 * max(1, abs(xs[0])) or abs(ys[0] - ys[1]) > 1e-3 * max(1, abs(ys[0])):
        return None
    return xs[-1] - xs[0], ys[-1] - ys[0]


def block_names(path: str | Path) -> Counter:
    """Count INSERTs per block name in modelspace, to let the user pick the panel block."""
    import ezdxf

    doc = ezdxf.readfile(str(path))
    return Counter(e.dxf.name for e in doc.modelspace().query("INSERT"))


def _cluster(values: list[float], tol: float) -> list[float]:
    centres: list[list[float]] = []
    for v in sorted(values):
        if centres and v - centres[-1][-1] <= tol:
            centres[-1].append(v)
        else:
            centres.append([v])
    return [sum(c) / len(c) for c in centres]


def _snap(centres: list[float], pitch: float) -> dict[float, int]:
    """Map cluster centres to integer indices, leaving gaps where a pitch is skipped."""
    index, out = 0, {}
    for i, c in enumerate(centres):
        if i:
            steps = max(1, round((c - centres[i - 1]) / pitch))
            index += steps
        out[c] = index
    return out


def grid_from_dxf(path: str | Path, block: str | None = None, layer: str | None = None,
                  rectangles: bool = False) -> DxfLayout:
    """Panels from copies of ``block`` (default: the most used block), or from
    closed rectangles when ``rectangles`` is set or the drawing has no blocks."""
    import ezdxf

    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    warnings: list[str] = []
    centres: list[tuple[float, float]] = []
    size: tuple[float, float] | None = None
    source = ""

    inserts = [e for e in msp.query("INSERT") if layer is None or e.dxf.layer == layer]
    if inserts and not rectangles:
        name = block or Counter(e.dxf.name for e in inserts).most_common(1)[0][0]
        chosen = [e for e in inserts if e.dxf.name == name]
        for e in chosen:
            try:
                box = ezdxf.bbox.extents([e])
            except Exception:  # noqa: BLE001 - odd blocks still give an insert point
                box = None
            if box is not None and box.has_data:
                centres.append(((box.extmin.x + box.extmax.x) / 2, (box.extmin.y + box.extmax.y) / 2))
                size = size or (box.size.x, box.size.y)
            else:
                centres.append((e.dxf.insert.x, e.dxf.insert.y))
        source = f"block '{name}' ({len(chosen)} inserts)"
    else:
        rects: list[tuple[tuple[float, float], tuple[float, float]]] = []
        for e in msp.query("LWPOLYLINE"):
            if layer is not None and e.dxf.layer != layer:
                continue
            pts = list(e.get_points("xy"))
            if not (e.closed or len(pts) == 5):
                continue
            rs = _rect_size(pts)
            if rs is None:
                continue
            cx = sum(p[0] for p in pts[:4]) / 4
            cy = sum(p[1] for p in pts[:4]) / 4
            rects.append(((round(rs[0], 3), round(rs[1], 3)), (cx, cy)))
        if not rects:
            raise LayoutError("No panel blocks or rectangles found in the DXF")
        common = Counter(r[0] for r in rects).most_common(1)[0][0]
        centres = [c for s, c in rects if s == common]
        size = common
        source = f"{len(centres)} rectangles {common[0]} x {common[1]}"
        if len(centres) < len(rects):
            warnings.append(f"Ignored {len(rects) - len(centres)} rectangles of a different size")

    if not centres:
        raise LayoutError("No panels found in the DXF")
    if size is None:
        xs = sorted({round(c[0], 3) for c in centres})
        ys = sorted({round(c[1], 3) for c in centres})
        size = (min((b - a for a, b in zip(xs, xs[1:])), default=1.0),
                min((b - a for a, b in zip(ys, ys[1:])), default=1.0))

    across, along = size
    col_centres = _cluster([c[0] for c in centres], across * 0.4)
    row_centres = _cluster([c[1] for c in centres], along * 0.4)
    col_pitch = min((b - a for a, b in zip(col_centres, col_centres[1:])), default=across)
    row_pitch = min((b - a for a, b in zip(row_centres, row_centres[1:])), default=along)
    col_index = _snap(col_centres, col_pitch)
    # Drawing Y grows upwards but grid row 0 is the top, so snap negated values.
    row_index = {-c: i for c, i in _snap(sorted(-c for c in row_centres), row_pitch).items()}

    n_cols = max(col_index.values()) + 1
    n_rows = max(row_index.values()) + 1
    grid = [[EMPTY] * n_cols for _ in range(n_rows)]

    def nearest(mapping: dict[float, int], v: float) -> int:
        return mapping[min(mapping, key=lambda c: abs(c - v))]

    clashes = 0
    for x, y in centres:
        r, c = nearest(row_index, y), nearest(col_index, x)
        if grid[r][c] == PANEL:
            clashes += 1
        grid[r][c] = PANEL
    if clashes:
        warnings.append(f"{clashes} panels overlapped another panel position and were merged")
    return DxfLayout(grid, len(centres) - clashes, source, size, warnings)
