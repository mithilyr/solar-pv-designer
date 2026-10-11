"""A design project: roofs, chosen equipment and site conditions, plus the
pipeline that turns them into a string design, BOM and guideline checks."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import __version__
from .bom import BomLine, BomResult, BomRules, compute_bom, roof_features, typical_run_length
from .checks import Check, lcrit_compulsory, run_checks, size_dc_cable, string_fuse
from .layout import PANEL, Grid, grid_size_m, panel_count
from .library import Inverter, Library, Panel
from .strings import DesignOptions, SiteConditions, StringDesign, design_strings

PROJECT_SUFFIX = ".spvd"


@dataclass
class Roof:
    name: str
    grid: Grid
    roof_type: str = "trapezoidal"
    dc_route_m: float | None = None  # one-way array-to-inverter cable route; None = estimate
    source: str = ""


@dataclass
class InverterChoice:
    key: str
    qty: int


@dataclass
class Project:
    name: str = "New project"
    client: str = ""
    location: str = ""
    designer: str = ""
    site_preset: str = ""
    site: SiteConditions = field(default_factory=SiteConditions)
    thunder_days: float = 100.0
    panel_key: str = ""
    inverters: list[InverterChoice] = field(default_factory=list)
    string_sizes: list[int] = field(default_factory=list)  # empty = automatic
    follow_runs: bool = True  # prefer the roof's typical run length as string length
    options: DesignOptions = field(default_factory=DesignOptions)
    cable_margin_pct: float = 30.0
    vertical_drop_m: float = 10.0
    max_dc_drop_pct: float = 3.0
    roofs: list[Roof] = field(default_factory=list)
    manual_quantities: dict[str, float] = field(default_factory=dict)  # BOM description -> qty

    @property
    def panels(self) -> int:
        return sum(panel_count(r.grid) for r in self.roofs)

    def save(self, path: str | Path) -> None:
        data = asdict(self)
        data["app_version"] = __version__
        Path(path).write_text(json.dumps(data, indent=1), "utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        data = json.loads(Path(path).read_text("utf-8"))
        data.pop("app_version", None)
        data["site"] = SiteConditions(**data.get("site", {}))
        data["options"] = DesignOptions(**data.get("options", {}))
        data["inverters"] = [InverterChoice(**i) for i in data.get("inverters", [])]
        data["roofs"] = [Roof(**r) for r in data.get("roofs", [])]
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def estimate_route_m(grid: Grid, panel: Panel, vertical_drop_m: float) -> float:
    """Auto_Solar_BOM v4 estimate: half the array's width plus half its length,
    plus 20% for tray routing, plus the drop from roof to inverter."""
    w, h = grid_size_m(grid, panel.length_m, panel.width_m)
    return (w / 2 + h / 2) * 1.2 + vertical_drop_m


def snake_order(grid: Grid) -> list[tuple[int, int]]:
    """Panel cells column by column, down one column and up the next,
    so consecutive panels are physically next to each other."""
    cells = []
    for c in range(len(grid[0])):
        rows = range(len(grid)) if c % 2 == 0 else range(len(grid) - 1, -1, -1)
        cells.extend((r, c) for r in rows if grid[r][c] == PANEL)
    return cells


@dataclass
class RoofResult:
    roof: Roof
    features: dict
    route_m: float
    strings: int
    cable_mm2: float
    cable_per_colour_m: float
    string_map: dict[tuple[int, int], str] = field(default_factory=dict)  # (row, col) -> INVxx-STRyy


@dataclass
class ProjectResult:
    panel: Panel | None
    design: StringDesign | None
    design_error: str
    roofs: list[RoofResult]
    bom: BomResult
    checks: list[Check]
    site_quantities: dict[str, float]


def compute(project: Project, lib: Library, rules: BomRules) -> ProjectResult:
    panel = lib.panel(project.panel_key) if project.panel_key else None
    inverters: list[Inverter] = []
    for choice in project.inverters:
        inverters.extend([lib.inverter(choice.key)] * choice.qty)

    total_panels = project.panels
    design: StringDesign | None = None
    error = ""
    if panel is None:
        error = "Choose a panel"
    elif not inverters:
        error = "Choose inverters"
    elif total_panels == 0:
        error = "Add a roof layout"
    else:
        prefer = None
        if project.follow_runs and project.roofs and not project.string_sizes:
            biggest = max(project.roofs, key=lambda r: panel_count(r.grid))
            prefer = typical_run_length(biggest.grid)
        try:
            design = design_strings(panel, inverters, total_panels, project.site, project.options,
                                    sizes=project.string_sizes or None, prefer_size=prefer)
        except ValueError as exc:
            error = str(exc)

    # Map strings onto the roofs in snake order and size cables per roof.
    roof_results: list[RoofResult] = []
    schedule = design.schedule() if design else []
    queue = [(row["label"], row["panels"]) for row in schedule]
    qi, left_in_string = 0, queue[0][1] if queue else 0
    for roof in project.roofs:
        feats = roof_features(roof.grid)
        route = roof.dc_route_m if roof.dc_route_m else (estimate_route_m(roof.grid, panel, project.vertical_drop_m) if panel else 0.0)
        smap: dict[tuple[int, int], str] = {}
        starts = 0
        for cell in snake_order(roof.grid):
            if qi >= len(queue):
                break
            if left_in_string == queue[qi][1]:
                starts += 1
            smap[cell] = queue[qi][0]
            left_in_string -= 1
            if left_in_string == 0:
                qi += 1
                left_in_string = queue[qi][1] if qi < len(queue) else 0
        strings_here = starts if design else 0
        size, per_colour = 4.0, 0.0
        if panel and design:
            size, _ = size_dc_cable(route, panel, min(design.combination), project.site, project.max_dc_drop_pct)
            per_colour = strings_here * route * (1 + project.cable_margin_pct / 100)
        roof_results.append(RoofResult(roof, feats, route, strings_here, size, per_colour, smap))

    # Site-wide quantities for the BOM formulas.
    n_strings = design.total_strings if design else 0
    n_inv = len(design.inverters) if design else sum(c.qty for c in project.inverters)
    fuses = 0
    if design and panel:
        for a in design.inverters:
            for m in a.mppts:
                if m.strings and string_fuse(panel, m.strings)[0]:
                    fuses += 2 * m.strings
    longest_route = max((r.route_m for r in roof_results), default=0.0)
    site = {
        "panels": float(total_panels),
        "strings": float(n_strings),
        "inverters": float(n_inv),
        "kwp": total_panels * panel.pmax_w / 1000 if panel else 0.0,
        "string_fuses": float(fuses),
        "dc_spd_required": 1.0 if lcrit_compulsory(project.thunder_days, longest_route) else 0.0,
        "dc_cable_4mm_m": 0.0, "dc_cable_6mm_m": 0.0, "dc_cable_10mm_m": 0.0,
    }
    for rr in roof_results:
        site[f"dc_cable_{rr.cable_mm2:g}mm_m"] += rr.cable_per_colour_m
    roofs_for_bom = [(rr.roof.name, rr.roof.roof_type, rr.features) for rr in roof_results]
    fmt = {"panel_model": f"{panel.pmax_w:g}W {panel.brand} {panel.model}" if panel else ""}
    bom = compute_bom(rules, roofs_for_bom, site, fmt)
    for line in bom.lines:
        if line.description in project.manual_quantities:
            line.qty = project.manual_quantities[line.description]
            line.manual = False
    if design:
        for model, qty in _inverter_counts(design).items():
            bom.lines.insert(1, BomLine("PV Modules & Inverters", f"INVERTER {model}", "", "EA", float(qty),
                                        "From the string design"))

    checks = run_checks(design, project.site, project.options, project.thunder_days, longest_route,
                        project.max_dc_drop_pct) if design else []
    return ProjectResult(panel, design, error, roof_results, bom, checks, site)


def _inverter_counts(design: StringDesign) -> dict[str, int]:
    counts: dict[str, int] = {}
    for a in design.inverters:
        counts[a.inverter.key] = counts.get(a.inverter.key, 0) + 1
    return counts
