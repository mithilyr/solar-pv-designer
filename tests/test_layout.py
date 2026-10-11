import ezdxf
import pytest

from solarpv.dxf_import import grid_from_dxf
from solarpv.layout import LayoutError, RoofDimensions, grid_from_dimensions, normalise, panel_count, read_csv


def test_csv_with_ragged_rows(tmp_path):
    p = tmp_path / "roof.csv"
    p.write_text("1,1,1\n1,1,\n\n1,,\n")
    assert read_csv(p) == [[1, 1, 1], [1, 1, 0], [1, 0, 0]]


def test_bad_cells_are_rejected():
    with pytest.raises(LayoutError):
        normalise([["1", "x"]])
    with pytest.raises(LayoutError):
        normalise([["5"]])


def test_grid_from_dimensions():
    d = RoofDimensions(length_m=10, width_m=6, edge_setback_m=0.5, panel_length_m=2.278, panel_width_m=1.134, gap_m=0.02)
    grid = grid_from_dimensions(d)
    assert len(grid) == 3  # 9 m usable / 2.298 m
    assert len(grid[0]) == 4  # 5 m usable / 1.154 m
    assert panel_count(grid) == 12


def test_dimensions_with_walkways_and_slope():
    flat = grid_from_dimensions(RoofDimensions(30, 20, walkway_every_cols=5))
    assert any(all(row[c] == 0 for row in flat) for c in range(len(flat[0])))
    sloped = grid_from_dimensions(RoofDimensions(30, 20, slope_deg=30))
    assert len(sloped) > len(grid_from_dimensions(RoofDimensions(30, 20)))


def _panel_block_dxf(path, cells):
    doc = ezdxf.new()
    block = doc.blocks.new("PV_595")
    block.add_lwpolyline([(0, 0), (1134, 0), (1134, 2278), (0, 2278)], close=True)
    msp = doc.modelspace()
    for r, c in cells:
        msp.add_blockref("PV_595", (c * 1154, -r * 2298))
    msp.add_line((0, 0), (5000, 0))  # other drawing content is ignored
    doc.saveas(path)


def test_dxf_blocks_to_grid(tmp_path):
    cells = [(r, c) for r in range(3) for c in range(4) if (r, c) != (1, 2)] + [(4, 0)]
    path = tmp_path / "layout.dxf"
    _panel_block_dxf(path, cells)
    result = grid_from_dxf(path)
    assert result.panel_count == len(cells)
    assert result.grid[0] == [1, 1, 1, 1]
    assert result.grid[1] == [1, 1, 0, 1]
    assert result.grid[3] == [0, 0, 0, 0]  # the skipped row is kept as a gap
    assert result.grid[4][0] == 1


def test_dxf_rectangles_to_grid(tmp_path):
    doc = ezdxf.new()
    msp = doc.modelspace()
    for r in range(2):
        for c in range(3):
            x, y = c * 1.2, r * 2.3
            msp.add_lwpolyline([(x, y), (x + 1.134, y), (x + 1.134, y + 2.278), (x, y + 2.278)], close=True)
    path = tmp_path / "rects.dxf"
    doc.saveas(path)
    result = grid_from_dxf(path)
    assert result.grid == [[1, 1, 1], [1, 1, 1]]
