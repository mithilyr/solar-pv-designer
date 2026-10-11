"""Roof layout grids.

A layout is a grid with one cell per panel position, the same format as the
CSV files the BOM calculator has always used:

* ``1`` a panel
* ``0`` empty space or walkway
* ``2`` a special cell (kept from the original CSVs; it never holds a panel but
  counts as a panel edge for earth lug kits)

Grid columns are the rail runs: panels in one column, one after another, share
the same pair of rails. Layouts can be read from CSV or Excel, imported from an
AutoCAD DXF, or generated from roof dimensions measured in Google Earth.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

EMPTY, PANEL, SPECIAL = 0, 1, 2

Grid = list[list[int]]


class LayoutError(ValueError):
    pass


def normalise(rows: list[list]) -> Grid:
    """Turn raw cells into a rectangular int grid, dropping blank rows."""
    grid: Grid = []
    for row in rows:
        cells = ["" if c is None else str(c).strip() for c in row]
        if not any(cells):
            continue
        out = []
        for c in cells:
            if c == "":
                out.append(EMPTY)
                continue
            try:
                value = int(float(c))
            except ValueError as exc:
                raise LayoutError(f"Cell value {c!r} is not 0, 1 or 2") from exc
            if value not in (EMPTY, PANEL, SPECIAL):
                raise LayoutError(f"Cell value {value} is not 0, 1 or 2")
            out.append(value)
        grid.append(out)
    if not grid:
        raise LayoutError("The layout is empty")
    width = max(len(r) for r in grid)
    return [r + [EMPTY] * (width - len(r)) for r in grid]


def read_csv(path: str | Path) -> Grid:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return normalise(list(csv.reader(f)))


def read_excel(path: str | Path, sheet: str | None = None) -> Grid:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    return normalise([list(r) for r in ws.iter_rows(values_only=True)])


def read_layout(path: str | Path) -> Grid:
    suffix = Path(path).suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return read_excel(path)
    if suffix == ".dxf":
        from .dxf_import import grid_from_dxf

        return grid_from_dxf(path).grid
    return read_csv(path)


def write_csv(grid: Grid, path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(grid)


def panel_count(grid: Grid) -> int:
    return sum(row.count(PANEL) for row in grid)


@dataclass
class RoofDimensions:
    """A roof face measured in Google Earth (or from drawings).

    ``length_m`` runs along the rails (down the slope for a pitched roof);
    ``width_m`` runs across the roof, one rail run per panel column.
    """

    length_m: float
    width_m: float
    edge_setback_m: float = 0.5
    panel_length_m: float = 2.333
    panel_width_m: float = 1.134
    portrait: bool = True
    gap_m: float = 0.02
    max_run_panels: int = 0  # break each run after this many panels (0 = no limit)
    run_break_m: float = 0.6  # gap left at each run break
    walkway_every_cols: int = 0  # walkway column after this many panel columns (0 = none)
    walkway_width_m: float = 0.6
    slope_deg: float = 0.0  # Google Earth gives plan view; pitched faces are longer

    def along_rail_m(self) -> float:
        """Usable length along the slope, corrected from the plan-view length."""
        true_length = self.length_m / math.cos(math.radians(self.slope_deg))
        return true_length - 2 * self.edge_setback_m

    def across_m(self) -> float:
        return self.width_m - 2 * self.edge_setback_m


def grid_from_dimensions(d: RoofDimensions) -> Grid:
    """Fit panels into a rectangular roof face and return the layout grid."""
    p_along = d.panel_length_m if d.portrait else d.panel_width_m
    p_across = d.panel_width_m if d.portrait else d.panel_length_m
    along, across = d.along_rail_m(), d.across_m()
    if along < p_along or across < p_across:
        raise LayoutError("The roof is too small for a single panel after setbacks")

    # Rows along the rails, with optional breaks between runs.
    rows: list[int] = []
    used, in_run = 0.0, 0
    while True:
        need = p_along + (d.gap_m if in_run else 0.0)
        if d.max_run_panels and in_run == d.max_run_panels:
            need = d.run_break_m + p_along
            if used + need > along + 1e-9:
                break
            rows.append(EMPTY)
            used += d.run_break_m
            in_run = 0
            continue
        if used + need > along + 1e-9:
            break
        rows.append(PANEL)
        used += need
        in_run += 1

    # Columns across the roof, with optional walkway columns.
    cols: list[int] = []
    used, since_walkway = 0.0, 0
    while True:
        if d.walkway_every_cols and since_walkway == d.walkway_every_cols:
            if used + d.walkway_width_m + p_across > across + 1e-9:
                break
            cols.append(EMPTY)
            used += d.walkway_width_m
            since_walkway = 0
            continue
        need = p_across + (d.gap_m if cols and cols[-1] == PANEL else 0.0)
        if used + need > across + 1e-9:
            break
        cols.append(PANEL)
        used += need
        since_walkway += 1

    return [[PANEL if (r == PANEL and c == PANEL) else EMPTY for c in cols] for r in rows]


def grid_size_m(grid: Grid, panel_length_m: float, panel_width_m: float, gap_m: float = 0.05) -> tuple[float, float]:
    """Approximate (across, along) size of a portrait layout in metres."""
    rows, cols = len(grid), len(grid[0])
    return cols * (panel_width_m + gap_m), rows * (panel_length_m + gap_m)
