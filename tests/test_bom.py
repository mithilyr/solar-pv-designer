"""The trapezoidal rules must reproduce Auto_Solar_BOM v4 exactly.

Expected numbers were produced by running Auto_Solar_BOM v4.py on these grids.
"""

import pytest

from solarpv.bom import compute_bom, roof_features
from solarpv.formula import FormulaError, evaluate

WALKWAY_AND_BREAKS = [
    [1, 1, 1, 0, 1, 1, 1, 1],
    [1, 1, 1, 0, 1, 1, 1, 1],
    [1, 1, 1, 0, 1, 1, 1, 1],
    [0, 0, 0, 0, 0, 0, 0, 0],
    [1, 1, 1, 0, 1, 1, 1, 1],
    [1, 1, 1, 0, 1, 1, 1, 1],
    [2, 1, 1, 0, 1, 1, 1, 1],
    [0, 1, 1, 0, 1, 1, 1, 1],
    [1, 1, 1, 0, 1, 1, 1, 1],
    [1, 1, 1, 0, 1, 1, 1, 1],
    [1, 1, 1, 0, 1, 1, 1, 1],
    [1, 1, 1, 0, 1, 1, 1, 1],
]
LONG_RUNS = [[1] * 6 for _ in range(14)] + [[1, 1, 1, 0, 0, 0]]

V4 = {
    "walkway_and_breaks": dict(panels=75, end=60, mid=120, earth_clips=60, rails=62.6, trapezoid=150,
                               walkway_bases=78, lugs=15, plates=13, connectors=12, screws=228),
    "long_runs": dict(panels=87, end=24, mid=162, earth_clips=81, rails=69.6, trapezoid=174,
                      walkway_bases=0, lugs=0, plates=0, connectors=24, screws=174),
}

SITE = dict(panels=0, strings=0, inverters=1, kwp=0, string_fuses=0, dc_spd_required=0,
            dc_cable_4mm_m=0, dc_cable_6mm_m=0, dc_cable_10mm_m=0)


def raw(result, words):
    for line in result.lines:
        if words in line.description:
            return line.raw_qty
    raise AssertionError(words)


@pytest.mark.parametrize("name,grid", [("walkway_and_breaks", WALKWAY_AND_BREAKS), ("long_runs", LONG_RUNS)])
def test_trapezoidal_matches_v4(rules, name, grid):
    f = roof_features(grid)
    site = {**SITE, "panels": f["panels"]}
    result = compute_bom(rules, [(name, "trapezoidal", f)], site, {"panel_model": "595W"})
    assert not result.errors
    v4 = V4[name]
    assert raw(result, "SOLAR PANEL") == v4["panels"]
    assert raw(result, "end clamp") == v4["end"]
    assert raw(result, "mid clamp") == v4["mid"]
    assert raw(result, "GROUNDING CLIP") == v4["earth_clips"]
    assert raw(result, "MOUNTING PROFILE") == pytest.approx(v4["rails"])
    assert raw(result, "TREPOZIDE SERRATED") == v4["trapezoid"]
    assert (raw(result, "ROOF BASE 60mm") or 0) == v4["walkway_bases"]
    assert raw(result, "LUG KIT") == v4["lugs"]
    assert (raw(result, "CHECKER PLATE") or 0) == v4["plates"]
    assert raw(result, "CONNECTOR CLAMP") == v4["connectors"]
    assert raw(result, "SCREW GI AMANO") == v4["screws"]


def test_quantities_round_up(rules):
    f = roof_features(WALKWAY_AND_BREAKS)
    result = compute_bom(rules, [("r", "trapezoidal", f)], {**SITE, "panels": f["panels"]})
    rails = next(l for l in result.lines if "MOUNTING PROFILE" in l.description)
    assert rails.qty == 63


def test_every_roof_type_evaluates(rules):
    f = roof_features(WALKWAY_AND_BREAKS)
    for key in rules.roof_types:
        result = compute_bom(rules, [("r", key, f)], {**SITE, "panels": f["panels"]})
        assert not result.errors, (key, result.errors)


def test_roofs_add_up(rules):
    f = roof_features(LONG_RUNS)
    one = compute_bom(rules, [("a", "trapezoidal", f)], SITE)
    two = compute_bom(rules, [("a", "trapezoidal", f), ("b", "trapezoidal", f)], SITE)
    assert raw(two, "mid clamp") == 2 * raw(one, "mid clamp")


def test_formula_is_safe():
    assert evaluate("ceil(a / 4) + max(b, 2)", {"a": 9, "b": 1}) == 5
    assert evaluate("2 if a > 1 else 3", {"a": 2}) == 2
    with pytest.raises(FormulaError):
        evaluate("__import__('os').system('echo hi')", {})
    with pytest.raises(FormulaError):
        evaluate("panels.real", {"panels": 1})
    with pytest.raises(FormulaError):
        evaluate("unknown + 1", {})
