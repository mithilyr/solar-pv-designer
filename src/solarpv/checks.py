"""Design checks against the Sri Lankan rooftop solar guidelines.

References are to the PUCSL "Guidelines on Rooftop Solar PV Installation for
Solar Service Providers", Revision 1, September 2022 (called the PUCSL
guideline below), which follows BS 7671 and IEC 62548.
"""

from __future__ import annotations

from dataclasses import dataclass

from .library import Panel
from .strings import DesignOptions, SiteConditions, StringDesign, isc_at, string_limits, vmp_at, voc_at

GUIDE = "PUCSL rooftop solar guideline (Rev 1, 2022)"
COPPER_RESISTIVITY = 0.0225  # ohm·mm²/m, copper near 70 °C operating temperature
DC_CABLE_SIZES = (4.0, 6.0, 10.0)
FUSE_SIZES = (10, 12, 15, 16, 20, 25, 30, 32, 40)


@dataclass
class Check:
    title: str
    status: str  # "pass", "warn", "fail", "info"
    detail: str
    reference: str = ""


def dc_voltage_drop_pct(one_way_m: float, current_a: float, area_mm2: float, string_v: float) -> float:
    return 2 * one_way_m * current_a * COPPER_RESISTIVITY / area_mm2 / string_v * 100


def size_dc_cable(one_way_m: float, panel: Panel, string_size: int, site: SiteConditions,
                  max_drop_pct: float = 3.0) -> tuple[float, float]:
    """Smallest DC cable (mm²) keeping the string's voltage drop under the limit. Returns (size, drop %)."""
    v = vmp_at(panel, site.max_cell_c) * string_size
    for size in DC_CABLE_SIZES:
        drop = dc_voltage_drop_pct(one_way_m, panel.imp_a, size, v)
        if drop <= max_drop_pct:
            return size, drop
    return DC_CABLE_SIZES[-1], dc_voltage_drop_pct(one_way_m, panel.imp_a, DC_CABLE_SIZES[-1], v)


# Guideline 3.4.2: Lcrit = factor / Ng, by type of installation.
INSTALLATION_TYPES = {
    "building": ("Service, industrial or agricultural building", 450),
    "residential": ("Individual residential premises", 115),
    "ground": ("Ground-mounted (terrestrial) plant", 200),
}

# Guideline Annex A (thunder-day map, 1961-1990). Each district takes the top of
# the map band it mostly falls in, so the surge check errs on the safe side.
THUNDER_DAYS_BY_DISTRICT = {
    "Ampara": 85, "Anuradhapura": 85, "Badulla": 85, "Batticaloa": 85, "Colombo": 110, "Galle": 110,
    "Gampaha": 110, "Hambantota": 110, "Jaffna": 35, "Kalutara": 110, "Kandy": 110, "Kegalle": 110,
    "Kilinochchi": 50, "Kurunegala": 110, "Mannar": 60, "Matale": 110, "Matara": 85, "Moneragala": 110,
    "Mullaitivu": 60, "Nuwara Eliya": 85, "Polonnaruwa": 85, "Puttalam": 110, "Ratnapura": 110,
    "Trincomalee": 60, "Vavuniya": 60,
}
SPD_SECOND_DEVICE_M = 10.0  # guideline 3.4.2: over 10 m, a second SPD near the modules


def lcrit_m(thunder_days: float, installation: str = "building") -> float:
    """Critical DC cable length: Lcrit = factor / Ng, Ng = 0.04 Td^1.25.

    The guideline prints the exponent as 1.12; IEC 62305-2, which it cites,
    uses 1.25. 1.25 gives a higher Ng and a shorter Lcrit, so it is the safer
    choice of the two.
    """
    ng = 0.04 * thunder_days ** 1.25
    return INSTALLATION_TYPES.get(installation, INSTALLATION_TYPES["building"])[1] / ng


@dataclass
class SpdPlan:
    compulsory: bool
    lcrit_m: float
    inverter_end: int  # DC SPDs to buy for the inverter end (one per MPPT in use)
    array_end: int  # DC SPDs to buy near the modules (one per MPPT in use)
    array_boxes: int = 0  # enclosures near the modules (one per inverter)


def spd_plan(design: StringDesign, route_m: float, thunder_days: float, installation: str = "building") -> SpdPlan:
    """Where DC SPDs go (guideline 3.4.2). Inverters with a built-in Type II
    arrester need none at the inverter end; routes over 10 m need a second set
    in a box close to the modules. SPDs protect each MPPT input, + and -."""
    lc = lcrit_m(thunder_days, installation)
    if route_m < lc:
        return SpdPlan(False, lc, 0, 0)
    inverter_end = array_end = boxes = 0
    for a in design.inverters:
        used = sum(1 for m in a.mppts if m.strings)
        if not a.inverter.dc_spd_builtin:
            inverter_end += used
        if route_m > SPD_SECOND_DEVICE_M and used:
            array_end += used
            boxes += 1
    return SpdPlan(True, lc, inverter_end, array_end, boxes)


def string_fuse(panel: Panel, parallel_strings: int) -> tuple[bool, int | None]:
    """(fuses needed?, fuse rating A) for N strings in parallel on one MPPT (guideline 3.1)."""
    needed = (parallel_strings - 1) * panel.isc_a > panel.max_series_fuse_a
    if not needed:
        return False, None
    for size in FUSE_SIZES:
        if 1.5 * panel.isc_a < size <= min(2.4 * panel.isc_a, panel.max_series_fuse_a):
            return True, size
    return True, None


def run_checks(design: StringDesign, site: SiteConditions, options: DesignOptions, thunder_days: float,
               dc_route_m: float, max_dc_drop_pct: float = 3.0, installation: str = "building") -> list[Check]:
    panel = design.panel
    checks: list[Check] = []
    longest = max(design.combination)
    shortest = min(design.combination)

    unconnected = design.panels_available - design.panels_used
    if unconnected:
        checks.append(Check("Unconnected panels", "warn",
                            f"{unconnected} of {design.panels_available} panels are not in any string. Remove them from "
                            "the layout, add inverter capacity, or set the string lengths by hand.", "String design"))

    for a in design.inverters:
        inv = a.inverter
        lim = string_limits(panel, inv, site)
        tag = f"INV{a.number:02d} {inv.model}"
        voc = voc_at(panel, site.min_cell_c) * longest
        ok = voc <= min(inv.max_dc_voltage, panel.max_system_voltage_v)
        checks.append(Check(f"{tag}: cold open-circuit voltage",
                            "pass" if ok else "fail",
                            f"{longest} panels × {voc_at(panel, site.min_cell_c):.2f} V = {voc:.0f} V at {site.min_cell_c:.0f} °C "
                            f"(limit {min(inv.max_dc_voltage, panel.max_system_voltage_v):.0f} V)", GUIDE + " §2.1"))
        vmin = vmp_at(panel, site.max_cell_c) * shortest
        vmax = vmp_at(panel, site.min_cell_c) * longest
        ok = inv.mppt_min_v <= vmin and vmax <= inv.mppt_max_v
        checks.append(Check(f"{tag}: MPPT voltage window",
                            "pass" if ok else "fail",
                            f"Operating {vmin:.0f}–{vmax:.0f} V vs MPPT {inv.mppt_min_v:.0f}–{inv.mppt_max_v:.0f} V "
                            f"(allowed string length {lim.min_panels}–{lim.max_panels})", "Inverter datasheet"))
        for m in a.mppts:
            if not m.strings:
                continue
            i = m.index - 1
            imp = m.strings * panel.imp_a
            isc = m.strings * isc_at(panel, site.max_cell_c)
            status = "pass"
            if isc > inv.max_isc_per_mppt[i]:
                status = "fail"
            elif imp > inv.max_current_per_mppt[i]:
                status = "warn"
            if status != "pass":
                checks.append(Check(f"{tag} MPPT {m.index}: input current", status,
                                    f"{m.strings} strings: Imp {imp:.1f} A (max {inv.max_current_per_mppt[i]} A), "
                                    f"Isc {isc:.1f} A (max {inv.max_isc_per_mppt[i]} A)", "Inverter datasheet"))
        ol = a.overload_pct(panel)
        ok = options.min_overload_pct <= ol <= options.max_overload_pct
        checks.append(Check(f"{tag}: DC/AC overload", "pass" if ok else "warn",
                            f"{a.dc_kw(panel):.2f} kWp on {inv.ac_kw:g} kW = {ol:.1f}% "
                            f"(window {options.min_overload_pct}–{options.max_overload_pct}%)", "Company design rule"))
        if inv.max_pv_kwp and a.dc_kw(panel) > inv.max_pv_kwp:
            checks.append(Check(f"{tag}: maximum PV array power", "fail",
                                f"{a.dc_kw(panel):.1f} kWp exceeds {inv.max_pv_kwp} kWp", "Inverter datasheet"))

    # Component ratings (guideline 2.1, 2.3)
    v_rating = 1.15 * panel.voc_v * longest
    max_parallel = max((m.strings for a in design.inverters for m in a.mppts), default=1)
    checks.append(Check("DC component voltage rating", "info",
                        f"Cables, isolators, connectors and fuses must be rated above 1.15 × Voc(STC) × M = {v_rating:.0f} V DC",
                        GUIDE + " §2.1, §2.3"))
    checks.append(Check("String cable current rating", "info",
                        f"Iz after de-rating must exceed 1.25 × Isc = {1.25 * panel.isc_a:.1f} A "
                        f"(and {1.25 * panel.isc_a * max(1, max_parallel - 1):.1f} A without string fuses)",
                        GUIDE + " §2.3.2"))

    # String fuses (guideline 3.1)
    needed, rating = string_fuse(panel, max_parallel)
    if needed:
        checks.append(Check("String fuses", "warn" if rating else "fail",
                            f"{max_parallel} strings in parallel: (N−1) × Isc = {(max_parallel - 1) * panel.isc_a:.1f} A exceeds the "
                            f"module's {panel.max_series_fuse_a:g} A series fuse rating. "
                            + (f"Fit {rating} A gPV fuses in both legs." if rating else "No standard fuse fits 1.5–2.4 × Isc."),
                            GUIDE + " §3.1"))
    else:
        checks.append(Check("String fuses", "pass",
                            f"Not required: (N−1) × Isc = {(max_parallel - 1) * panel.isc_a:.1f} A ≤ {panel.max_series_fuse_a:g} A",
                            GUIDE + " §3.1"))

    # DC voltage drop (guideline 2.3)
    size, drop = size_dc_cable(dc_route_m, panel, shortest, site, max_dc_drop_pct)
    checks.append(Check("DC cable voltage drop", "pass" if drop <= max_dc_drop_pct else "fail",
                        f"Longest route {dc_route_m:.0f} m, shortest string {shortest} panels: {size:g} mm² gives {drop:.2f}% "
                        f"(limit {max_dc_drop_pct}%)", GUIDE + " §2.3"))

    # Surge protection (guideline 3.4.2)
    plan = spd_plan(design, dc_route_m, thunder_days, installation)
    kind = INSTALLATION_TYPES.get(installation, INSTALLATION_TYPES["building"])[0].lower()
    basis = (f"Cable length {dc_route_m:.0f} m vs Lcrit {plan.lcrit_m:.0f} m for a {kind} "
             f"(Td = {thunder_days:g} thunder days)")
    rating = (f"Rate each SPD Ucpv ≥ 1.25 × Voc(STC) × M = {1.25 * panel.voc_v * longest:.0f} V, Up ≤ 1.5 kV, "
              "In ≥ 40 kA, on both + and −.")
    if not plan.compulsory:
        checks.append(Check("DC surge protection", "pass", basis + ": DC SPD not compulsory.", GUIDE + " §3.4.2"))
    else:
        builtin = all(a.inverter.dc_spd_builtin for a in design.inverters)
        where = []
        if plan.inverter_end:
            where.append(f"{plan.inverter_end} at the inverter end (no built-in DC SPD)")
        elif builtin:
            where.append("inverter end covered by the inverters' built-in Type II DC SPD")
        if plan.array_end:
            where.append(f"{plan.array_end} near the modules in {plan.array_boxes} box(es), as the route is over "
                         f"{SPD_SECOND_DEVICE_M:g} m")
        checks.append(Check("DC surge protection", "info",
                            basis + ": Type 2 DC SPD compulsory. " + _sentence("; ".join(where))
                            + (rating if plan.inverter_end or plan.array_end else ""),
                            GUIDE + " §3.4.2"))
    checks.append(Check("Earthing", "info",
                        "Bond every module frame and rail to one continuous earth conductor per row; bare copper not less than 6 mm²",
                        GUIDE + " §3.4.1"))
    checks.append(Check("AC cable voltage drop", "info",
                        "Size the inverter-to-MDB cable for under 3% drop (1% recommended to avoid overvoltage tripping)",
                        GUIDE + " §3.3"))
    return checks


def _sentence(text: str) -> str:
    return (text[:1].upper() + text[1:] + ". ") if text else ""


def lcrit_compulsory(thunder_days: float, route_m: float, installation: str = "building") -> bool:
    return route_m >= lcrit_m(thunder_days, installation)

