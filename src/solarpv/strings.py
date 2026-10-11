"""String sizing and string-to-inverter allocation.

Builds on the 2026 Solar_String_Cal / solar_pv_designer logic:

* string lengths come from temperature-corrected module voltages against the
  inverter's DC and MPPT limits (PUCSL guideline section 2.1);
* the panel count is split into strings of two neighbouring lengths (e.g.
  12 and 13), trying every split;
* strings are spread across the inverters' MPPTs so each inverter sits inside
  the DC/AC overload window (18.5–31.5 % by default) and no MPPT exceeds its
  current or string limit. Strings in parallel on one MPPT always have the
  same length.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .library import Inverter, Panel


@dataclass
class SiteConditions:
    """Temperatures for voltage limits. Defaults suit Sri Lankan lowland sites."""

    min_ambient_c: float = 18.0
    max_ambient_c: float = 36.0
    cell_rise_c: float = 30.0  # cell temperature above ambient at full sun

    @property
    def min_cell_c(self) -> float:
        return self.min_ambient_c  # early morning, no irradiance heating

    @property
    def max_cell_c(self) -> float:
        return self.max_ambient_c + self.cell_rise_c


SITE_PRESETS = {
    "Lowland / wet zone (Colombo, Gampaha, Galle, Kurunegala)": SiteConditions(18, 36),
    "Dry zone (Jaffna, Anuradhapura, Hambantota, Trincomalee)": SiteConditions(20, 38),
    "Mid country (Kandy, Kegalle, Badulla)": SiteConditions(12, 33),
    "Hill country (Nuwara Eliya, Hatton, Bandarawela)": SiteConditions(0, 28),
}


def voc_at(panel: Panel, cell_c: float) -> float:
    return panel.voc_v * (1 + panel.temp_coeff_voc_pct / 100 * (cell_c - 25))


def vmp_at(panel: Panel, cell_c: float) -> float:
    # Datasheets rarely give a Vmp coefficient; the Pmax coefficient is a
    # slightly conservative stand-in (Vmp falls faster than Voc with heat).
    return panel.vmp_v * (1 + panel.temp_coeff_pmax_pct / 100 * (cell_c - 25))


def isc_at(panel: Panel, cell_c: float) -> float:
    return panel.isc_a * (1 + panel.temp_coeff_isc_pct / 100 * (cell_c - 25))


@dataclass
class StringLimits:
    min_panels: int
    max_panels: int
    reasons: list[str]


def string_limits(panel: Panel, inv: Inverter, site: SiteConditions) -> StringLimits:
    voc_cold = voc_at(panel, site.min_cell_c)
    vmp_cold = vmp_at(panel, site.min_cell_c)
    vmp_hot = vmp_at(panel, site.max_cell_c)
    v_max = min(inv.max_dc_voltage, panel.max_system_voltage_v)
    by_voc = math.floor(v_max / voc_cold)
    by_mppt_high = math.floor(inv.mppt_max_v / vmp_cold)
    by_mppt_low = math.ceil(inv.mppt_min_v / vmp_hot)
    by_start = math.ceil((inv.start_voltage or 0) / voc_at(panel, site.max_cell_c)) if inv.start_voltage else 1
    reasons = [
        f"Max {by_voc} panels: cold Voc {voc_cold:.2f} V/panel at {site.min_cell_c:.0f} °C vs {v_max:.0f} V limit",
        f"Max {by_mppt_high} panels: cold Vmp {vmp_cold:.2f} V/panel vs MPPT max {inv.mppt_max_v:.0f} V",
        f"Min {by_mppt_low} panels: hot Vmp {vmp_hot:.2f} V/panel at {site.max_cell_c:.0f} °C vs MPPT min {inv.mppt_min_v:.0f} V",
    ]
    return StringLimits(max(by_mppt_low, by_start, 1), min(by_voc, by_mppt_high), reasons)


def split_combinations(total_panels: int, sizes: list[int]) -> list[dict[int, int]]:
    """Every way to write total_panels as a sum of strings of the given sizes."""
    sizes = sorted(set(sizes))
    out: list[dict[int, int]] = []

    def rec(i: int, remaining: int, acc: dict[int, int]):
        if i == len(sizes) - 1:
            if remaining % sizes[i] == 0:
                out.append({**acc, sizes[i]: remaining // sizes[i]})
            return
        for n in range(remaining // sizes[i] + 1):
            rec(i + 1, remaining - n * sizes[i], {**acc, sizes[i]: n})

    if sizes:
        rec(0, total_panels, {})
    return [{k: v for k, v in c.items() if v} for c in out]


@dataclass
class MPPTAssignment:
    index: int  # 1-based
    string_size: int = 0
    strings: int = 0


@dataclass
class InverterAssignment:
    inverter: Inverter
    number: int  # 1-based across the site
    mppts: list[MPPTAssignment] = field(default_factory=list)

    @property
    def panels(self) -> int:
        return sum(m.string_size * m.strings for m in self.mppts)

    @property
    def strings(self) -> int:
        return sum(m.strings for m in self.mppts)

    def dc_kw(self, panel: Panel) -> float:
        return self.panels * panel.pmax_w / 1000

    def overload_pct(self, panel: Panel) -> float:
        return (self.dc_kw(panel) / self.inverter.ac_kw - 1) * 100


@dataclass
class DesignOptions:
    min_overload_pct: float = 18.5
    max_overload_pct: float = 31.5
    allow_mixed_sizes_per_inverter: bool = True
    max_panels_left_out: int = 10


@dataclass
class StringDesign:
    panel: Panel
    inverters: list[InverterAssignment]
    combination: dict[int, int]
    panels_used: int
    panels_available: int
    score: float
    warnings: list[str] = field(default_factory=list)

    @property
    def total_strings(self) -> int:
        return sum(self.combination.values())

    @property
    def dc_kwp(self) -> float:
        return self.panels_used * self.panel.pmax_w / 1000

    @property
    def ac_kw(self) -> float:
        return sum(a.inverter.ac_kw for a in self.inverters)

    def schedule(self) -> list[dict]:
        """One row per string, labelled INVxx-STRyy as on the string layout drawings."""
        rows = []
        for a in self.inverters:
            n = 0
            for m in a.mppts:
                for _ in range(m.strings):
                    n += 1
                    rows.append({
                        "label": f"INV{a.number:02d}-STR{n:02d}",
                        "inverter": a.number,
                        "model": a.inverter.key,
                        "mppt": m.index,
                        "string": n,
                        "panels": m.string_size,
                    })
        return rows


def _mppt_can_take(inv: Inverter, m: MPPTAssignment, size: int, panel: Panel, site: SiteConditions) -> bool:
    i = m.index - 1
    if m.strings and m.string_size != size:
        return False
    n = m.strings + 1
    if n > inv.strings_per_mppt[i]:
        return False
    if n * panel.imp_a > inv.max_current_per_mppt[i] + 1e-9:
        # Inverters clip current above the limit; allowed only up to the Isc rating.
        if n * isc_at(panel, site.max_cell_c) > inv.max_isc_per_mppt[i] + 1e-9:
            return False
    return True


def allocate(panel: Panel, inverters: list[Inverter], combo: dict[int, int], site: SiteConditions,
             options: DesignOptions) -> list[InverterAssignment] | None:
    """Greedy, deterministic allocation: biggest strings first, to the inverter
    furthest below its share of DC power, filling MPPTs of the same size first."""
    assignments = [
        InverterAssignment(inv, k + 1, [MPPTAssignment(j + 1) for j in range(inv.mppts)])
        for k, inv in enumerate(inverters)
    ]
    total_panels = sum(s * n for s, n in combo.items())
    total_ac = sum(i.ac_kw for i in inverters)
    target = [total_panels * i.ac_kw / total_ac for i in inverters]

    pending = sorted(((s, n) for s, n in combo.items()), reverse=True)
    for size, count in pending:
        for _ in range(count):
            best, best_key = None, None
            for k, a in enumerate(assignments):
                if not options.allow_mixed_sizes_per_inverter and any(
                        m.strings and m.string_size != size for m in a.mppts):
                    continue
                same = [m for m in a.mppts if m.strings and m.string_size == size
                        and _mppt_can_take(a.inverter, m, size, panel, site)]
                empty = [m for m in a.mppts if not m.strings and _mppt_can_take(a.inverter, m, size, panel, site)]
                slot = (same or empty or [None])[0]
                if slot is None:
                    continue
                deficit = target[k] - a.panels
                key = (deficit, bool(same), -k)
                if best_key is None or key > best_key:
                    best, best_key = (a, slot), key
            if best is None:
                return None
            a, slot = best
            slot.string_size = size
            slot.strings += 1
    return assignments


def _score(design: list[InverterAssignment], panel: Panel, options: DesignOptions, left_out: int) -> float:
    """Lower is better: even loading first, then tidy MPPT use, then fewer panels left out."""
    ols = [a.overload_pct(panel) for a in design]
    mean = sum(ols) / len(ols)
    variance = sum((o - mean) ** 2 for o in ols) / len(ols)
    mixed = sum(1 for a in design if len({m.string_size for m in a.mppts if m.strings}) > 1)
    odd_mppts = sum(1 for a in design for m in a.mppts if m.strings == 1 and a.inverter.strings_per_mppt[m.index - 1] > 1)
    return variance * 100 + mixed * 5 + odd_mppts * 1 + left_out * 50


def check_inverter(a: InverterAssignment, panel: Panel, options: DesignOptions) -> list[str]:
    problems = []
    ol = a.overload_pct(panel)
    if ol < options.min_overload_pct:
        problems.append(f"INV{a.number:02d} DC/AC overload {ol:.1f}% is below {options.min_overload_pct}%")
    if ol > options.max_overload_pct:
        problems.append(f"INV{a.number:02d} DC/AC overload {ol:.1f}% is above {options.max_overload_pct}%")
    if a.inverter.max_pv_kwp and a.dc_kw(panel) > a.inverter.max_pv_kwp + 1e-9:
        problems.append(f"INV{a.number:02d} has {a.dc_kw(panel):.1f} kWp, above the {a.inverter.max_pv_kwp} kWp maximum")
    return problems


def design_strings(panel: Panel, inverters: list[Inverter], panels_available: int, site: SiteConditions,
                   options: DesignOptions | None = None, sizes: list[int] | None = None,
                   prefer_size: int | None = None, max_combos: int = 400) -> StringDesign:
    """Find the best string plan for the panels on the roof and the chosen inverters.

    ``sizes`` restricts the string lengths; by default every pair of
    neighbouring lengths allowed by all inverters is tried. ``prefer_size``
    (usually the most common run length on the roof, so strings follow the
    rail runs) steers the choice towards the longest whole number of runs; otherwise longer strings
    win because they need less cable.
    """
    options = options or DesignOptions()
    if not inverters:
        raise ValueError("Choose at least one inverter")
    lims = [string_limits(panel, inv, site) for inv in inverters]
    lo = max(l.min_panels for l in lims)
    hi = min(l.max_panels for l in lims)
    if lo > hi:
        raise ValueError(f"No string length suits every inverter (needs {lo} to {hi} panels)")
    if prefer_size and prefer_size <= hi:
        # A string can cover several whole runs: use the longest multiple that fits.
        prefer_size = prefer_size * (hi // prefer_size)
    if sizes:
        bad = [s for s in sizes if not lo <= s <= hi]
        if bad:
            raise ValueError(f"String length {bad} is outside the allowed {lo} to {hi} panels")
        size_sets = [sorted(sizes)]
    else:
        size_sets = [[s] for s in range(lo, hi + 1)] + [[s, s + 1] for s in range(lo, hi)]
        if prefer_size and lo <= prefer_size <= hi:
            # Try the run length and its neighbours first; fall back to everything.
            near = [ss for ss in size_sets if prefer_size in ss]
            size_sets = near + [ss for ss in size_sets if ss not in near]

    best: StringDesign | None = None
    best_invalid: StringDesign | None = None
    for left_out in range(0, options.max_panels_left_out + 1):
        n = panels_available - left_out
        if n <= 0:
            break
        for k, size_set in enumerate(size_sets):
            if best is not None and prefer_size and k >= 3:
                break  # a design that follows the runs beats any other length
            combos = split_combinations(n, size_set)
            if len(combos) > max_combos:
                step = len(combos) / max_combos
                combos = [combos[int(i * step)] for i in range(max_combos)]
            for combo in combos:
                alloc = allocate(panel, inverters, combo, site, options)
                if alloc is None:
                    continue
                problems = [p for a in alloc for p in check_inverter(a, panel, options)]
                score = _score(alloc, panel, options, left_out)
                if prefer_size and lo <= prefer_size <= hi:
                    score += sum(abs(s - prefer_size) * c for s, c in combo.items()) / sum(combo.values()) * 20
                else:
                    # Longer strings: fewer strings means less cable and labour.
                    score += sum(combo.values()) * 0.01
                d = StringDesign(panel, alloc, combo, n, panels_available, score, problems)
                if problems:
                    # Leaving panels out never fixes undersized inverters, so only
                    # keep fallbacks that use every panel we could connect.
                    if best_invalid is None or (best_invalid.panels_used == n and (
                            len(problems) < len(best_invalid.warnings) or (
                            len(problems) == len(best_invalid.warnings) and score < best_invalid.score))):
                        best_invalid = d
                elif best is None or score < best.score:
                    best = d
        if best is not None:
            break
    result = best or best_invalid
    if result is None:
        raise ValueError("These inverters cannot take this many strings. Add inverters or choose larger ones.")
    if result.panels_used < panels_available:
        result.warnings.insert(0, f"{panels_available - result.panels_used} panel(s) left unconnected so the strings split evenly")
    return result


def suggest_inverters(panel: Panel, panels: int, models: list[Inverter], options: DesignOptions | None = None,
                      target_overload_pct: float = 25.0, grid_voltage_v: float | None = 400) -> list[tuple[Inverter, int, float]]:
    """Single-model suggestions (model, quantity, overload %) inside the overload window, best first."""
    options = options or DesignOptions()
    kwp = panels * panel.pmax_w / 1000
    out = []
    for inv in models:
        if grid_voltage_v and inv.ac_voltage_v != grid_voltage_v:
            continue
        for qty in range(max(1, math.floor(kwp / (inv.ac_kw * (1 + options.max_overload_pct / 100)))),
                         math.ceil(kwp / inv.ac_kw) + 2):
            ol = (kwp / (qty * inv.ac_kw) - 1) * 100
            if options.min_overload_pct <= ol <= options.max_overload_pct:
                out.append((inv, qty, ol))
    out.sort(key=lambda t: (t[1], abs(t[2] - target_overload_pct)))
    return out
