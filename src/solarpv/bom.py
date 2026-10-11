"""Bill of materials from layout features and editable rules.

Quantities are formulas over named quantities measured from each roof's grid
(see :func:`roof_features`) and from the string design. Each roof type has its
own mounting rules; the electrical, earthing and safety items are common to
every site. Rules live in ``bom_rules.json``; an edited copy in the user data
folder overrides the built-in one.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from .formula import FormulaError, evaluate
from .layout import EMPTY, PANEL, SPECIAL, Grid
from .library import user_data_dir

FEATURE_HELP = {
    "panels": "Panels on the roof (cells with 1)",
    "runs": "Rail runs: unbroken lines of panels in a grid column",
    "runs_6_to_10": "Runs of 6 to 10 panels (need 2 rail joints)",
    "runs_11_plus": "Runs of 11 or more panels (need 4 rail joints)",
    "empty_column_cells": "Cells in columns with no panels at all (walkways)",
    "gaps_101": "Places where a run is broken by one empty cell (panel, gap, panel)",
    "gaps_201": "Breaks next to a special cell (2, gap, panel or panel, gap, 2)",
    "rows": "Grid rows",
    "cols": "Grid columns",
    "strings": "Strings (from the string design)",
    "inverters": "Inverters (from the string design)",
    "kwp": "DC capacity in kWp",
    "dc_cable_4mm_m": "Metres of 4 mm² DC cable per colour",
    "dc_cable_6mm_m": "Metres of 6 mm² DC cable per colour",
    "dc_cable_10mm_m": "Metres of 10 mm² DC cable per colour",
    "string_fuses": "gPV string fuses needed (both legs)",
    "dc_spd_required": "1 if a DC SPD is compulsory (cable length over Lcrit), else 0",
}


def roof_features(grid: Grid) -> dict[str, float]:
    """Measure a grid the way Auto_Solar_BOM v4 does."""
    rows, cols = len(grid), len(grid[0])
    f = dict(panels=0, runs=0, runs_6_to_10=0, runs_11_plus=0, empty_column_cells=0,
             gaps_101=0, gaps_201=0, rows=rows, cols=cols)
    run_lengths: list[int] = []
    for c in range(cols):
        column = [grid[r][c] for r in range(rows)]
        if all(v == EMPTY for v in column):
            f["empty_column_cells"] += rows
            continue
        r = 0
        while r < rows:
            if column[r] == PANEL:
                length = 0
                while r < rows and column[r] == PANEL:
                    length += 1
                    r += 1
                run_lengths.append(length)
            else:
                r += 1
        for r in range(rows - 2):
            a, b, cc = column[r], column[r + 1], column[r + 2]
            if a == PANEL and b == EMPTY and cc == PANEL:
                f["gaps_101"] += 1
            elif (a == SPECIAL and b == EMPTY and cc == PANEL) or (a == PANEL and b == EMPTY and cc == SPECIAL):
                f["gaps_201"] += 1
    f["panels"] = sum(run_lengths)
    f["runs"] = len(run_lengths)
    f["runs_6_to_10"] = sum(1 for n in run_lengths if 6 <= n <= 10)
    f["runs_11_plus"] = sum(1 for n in run_lengths if n >= 11)
    f["_run_lengths"] = run_lengths  # type: ignore[assignment]
    return f


def typical_run_length(grid: Grid) -> int | None:
    lengths = roof_features(grid)["_run_lengths"]
    if not lengths:
        return None
    return max(set(lengths), key=lambda n: (lengths.count(n), n))


@dataclass
class BomItem:
    category: str
    description: str
    qty: str  # formula; empty means entered by hand
    unit: str = "EA"
    code: str = ""
    round: str = "ceil"
    note: str = ""


@dataclass
class RoofType:
    key: str
    label: str
    variables: dict[str, str]
    items: list[BomItem]


@dataclass
class BomRules:
    common_items: list[BomItem]
    roof_types: dict[str, RoofType]
    spares_pct: float = 0.0

    def to_json(self) -> dict:
        def item(i: BomItem) -> dict:
            return dict(category=i.category, code=i.code, description=i.description, unit=i.unit,
                        qty=i.qty, round=i.round, note=i.note)
        return {
            "version": 1,
            "spares_pct": self.spares_pct,
            "common_items": [item(i) for i in self.common_items],
            "roof_types": {k: {"label": rt.label, "variables": rt.variables, "items": [item(i) for i in rt.items]}
                           for k, rt in self.roof_types.items()},
        }


def _items(data: list[dict]) -> list[BomItem]:
    return [BomItem(category=d["category"], description=d["description"], qty=d.get("qty", ""),
                    unit=d.get("unit", "EA"), code=str(d.get("code", "")), round=d.get("round", "ceil"),
                    note=d.get("note", "")) for d in data]


def rules_from_json(data: dict) -> BomRules:
    roof_types = {k: RoofType(k, v["label"], dict(v.get("variables", {})), _items(v["items"]))
                  for k, v in data["roof_types"].items()}
    return BomRules(_items(data["common_items"]), roof_types, float(data.get("spares_pct", 0)))


def user_rules_path() -> Path:
    return user_data_dir() / "bom_rules.json"


def load_rules(path: Path | None = None, use_user_copy: bool = True) -> BomRules:
    if path is None and use_user_copy and user_rules_path().exists():
        path = user_rules_path()
    if path is not None:
        return rules_from_json(json.loads(Path(path).read_text("utf-8")))
    return rules_from_json(json.loads(resources.files("solarpv.data").joinpath("bom_rules.json").read_text("utf-8")))


def save_user_rules(rules: BomRules) -> Path:
    path = user_rules_path()
    path.write_text(json.dumps(rules.to_json(), indent=1, ensure_ascii=False), "utf-8")
    return path


@dataclass
class BomLine:
    category: str
    description: str
    code: str
    unit: str
    qty: float | None  # None = enter by hand
    note: str = ""
    raw_qty: float | None = None
    manual: bool = False
    round_mode: str = "ceil"


@dataclass
class BomResult:
    lines: list[BomLine]
    errors: list[str] = field(default_factory=list)


def _round(value: float, mode: str, spares_pct: float, unit: str) -> float:
    if spares_pct and unit.upper() != "M":
        value *= 1 + spares_pct / 100
    if mode == "ceil":
        return float(math.ceil(value - 1e-9))
    if mode == "round":
        return float(round(value))
    return round(value, 2)


def _evaluate_items(items: list[BomItem], names: dict[str, float], fmt: dict[str, str],
                    acc: dict[tuple, BomLine], order: list[tuple], errors: list[str], where: str) -> None:
    for item in items:
        description = item.description.format_map(_SafeFormat(fmt))
        key = (item.category, description, item.code, item.unit)
        if key not in acc:
            acc[key] = BomLine(item.category, description, item.code, item.unit, None, item.note,
                               manual=not item.qty.strip(), round_mode=item.round)
            order.append(key)
        if not item.qty.strip():
            continue
        try:
            value = float(evaluate(item.qty, names))
        except (FormulaError, ZeroDivisionError, TypeError) as exc:
            errors.append(f"{where}: '{description}': {exc}")
            continue
        line = acc[key]
        line.raw_qty = (line.raw_qty or 0.0) + value


class _SafeFormat(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def compute_bom(rules: BomRules, roofs: list[tuple[str, str, dict[str, float]]], site: dict[str, float],
                fmt: dict[str, str] | None = None) -> BomResult:
    """roofs: (roof name, roof type key, features incl. per-roof cable metres);
    site: site-wide quantities (panels, strings, inverters, kwp, cables, ...)."""
    fmt = fmt or {}
    acc: dict[tuple, BomLine] = {}
    order: list[tuple] = []
    errors: list[str] = []
    for name, roof_type, features in roofs:
        rt = rules.roof_types.get(roof_type)
        if rt is None:
            errors.append(f"{name}: unknown roof type '{roof_type}'")
            continue
        names = {k: v for k, v in {**site, **features}.items() if not k.startswith("_")}
        for var, expr in rt.variables.items():
            try:
                names[var] = float(evaluate(expr, names))
            except (FormulaError, ZeroDivisionError, TypeError) as exc:
                errors.append(f"{name}: variable '{var}': {exc}")
                names[var] = 0.0
        _evaluate_items(rt.items, names, fmt, acc, order, errors, name)
    site_names = {k: v for k, v in site.items() if not k.startswith("_")}
    _evaluate_items(rules.common_items, site_names, fmt, acc, order, errors, "Site")

    lines = []
    for key in order:
        line = acc[key]
        if line.raw_qty is not None:
            line.manual = False
            line.qty = _round(line.raw_qty, line.round_mode, rules.spares_pct, line.unit)
        lines.append(line)
    category_rank = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    lines.sort(key=lambda l: category_rank.get(l.category, len(CATEGORY_ORDER)))
    return BomResult(lines, errors)


CATEGORY_ORDER = [
    "PV Modules & Inverters",
    "Mounting Structure",
    "Walking Path",
    "DC & AC Electrical",
    "Earthing & Lightning Protection",
    "Civil, Plumbing & Chemicals",
    "Health, Safety & Environment",
]
