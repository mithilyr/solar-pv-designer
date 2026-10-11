from solarpv.checks import lcrit_m, size_dc_cable, string_fuse
from solarpv.strings import DesignOptions, SiteConditions, design_strings, split_combinations, string_limits


def test_split_combinations():
    combos = split_combinations(25, [12, 13])
    assert combos == [{12: 1, 13: 1}]
    assert all(sum(s * n for s, n in c.items()) == 50 for c in split_combinations(50, [12, 13, 14]))


def test_string_limits_follow_datasheets(library, panel):
    inv = library.inverter("SolaX X3-FTH-125K")
    lim = string_limits(panel, inv, SiteConditions(18, 36))
    # 52.2 V × (1 + 0.0026 × 7) = 53.15 V per panel when cold; 1100 V / 53.15 V = 20.7
    assert lim.max_panels == 20
    hill = string_limits(panel, inv, SiteConditions(0, 28))
    assert hill.max_panels < lim.max_panels


def test_1500kwp_site_matches_the_built_design(library, panel):
    """A 1500.59 kWp roof: 2522 × 595 W on 10 × SolaX X3-FORTH 125 kW, built as
    20 strings of 12 plus one of 13 on most inverters (rail runs of 12 panels)."""
    inv = library.inverter("SolaX X3-FTH-125K")
    d = design_strings(panel, [inv] * 10, 2522, SiteConditions(), prefer_size=12)
    assert d.panels_used == 2522
    assert not d.warnings
    assert set(d.combination) <= {12, 13}
    assert d.combination[12] * 12 + d.combination.get(13, 0) * 13 == 2522
    for a in d.inverters:
        assert a.strings <= 24
        assert 18.5 <= a.overload_pct(panel) <= 31.5
        for m in a.mppts:
            assert m.strings <= 2
    labels = [s["label"] for s in d.schedule()]
    assert labels[0] == "INV01-STR01" and len(labels) == len(set(labels)) == d.total_strings


def test_undersized_inverters_are_flagged(library, panel):
    inv = library.inverter("SolaX X3-FTH-125K")
    d = design_strings(panel, [inv] * 3, 985, SiteConditions())
    assert d.panels_used == 985
    assert any("above 31.5%" in w for w in d.warnings)


def test_panels_left_out_when_strings_cannot_split(library, panel):
    inv = library.inverter("SolaX X3-MIC-10K-G2")
    d = design_strings(panel, [inv], 21, SiteConditions(), sizes=[10], options=DesignOptions(-100, 100))
    assert d.panels_used == 20
    assert "1 panel(s) left unconnected" in d.warnings[0]


def test_guideline_helpers(panel):
    assert string_fuse(panel, 2) == (False, None)  # (2-1) × 14.42 A ≤ 30 A
    needed, rating = string_fuse(panel, 4)  # 3 × 14.42 A = 43 A > 30 A
    assert needed and 1.5 * panel.isc_a < rating <= 30
    size, drop = size_dc_cable(150, panel, 12, SiteConditions())
    assert size >= 6 and drop <= 3
    assert 30 < lcrit_m(100) < 40


def test_mixed_inverters_share_an_overload_evenly(library, panel):
    # 841 panels (500 kWp) on 435 kW cannot reach the window; load every inverter evenly instead.
    invs = [library.inverter("SolaX X3-FTH-125K")] * 3 + [library.inverter("SolaX X3-MGA-60K-G2")]
    d = design_strings(panel, invs, 841, SiteConditions())
    ols = [a.overload_pct(panel) for a in d.inverters]
    assert max(ols) - min(ols) < 5


def test_small_sites_get_single_phase_suggestions(library, panel):
    from solarpv.strings import suggest_inverters

    found = suggest_inverters(panel, 14, [i for i in library.inverters if i.brand == "SolaX"])
    assert any(inv.ac_voltage_v == 230 for inv, _, _ in found)
    assert all(inv.ac_voltage_v <= 400 for inv, _, _ in found)


def test_strings_use_every_mppt_before_doubling_up(library, panel):
    inv = library.inverter("SolaX X3-MIC-12K-G2")
    d = design_strings(panel, [inv], 24, SiteConditions(), sizes=[12])
    assert [m.strings for m in d.inverters[0].mppts] == [1, 1]


def test_surge_protection_follows_guideline_3_4_2(library, panel):
    from solarpv.checks import spd_plan

    assert lcrit_m(100, "residential") < lcrit_m(100, "ground") < lcrit_m(100, "building")
    forth = library.inverter("SolaX X3-FTH-125K")
    mic = library.inverter("SolaX X3-MIC-12K-G2")
    assert forth.dc_spd_builtin and not mic.dc_spd_builtin
    d = design_strings(panel, [forth], 252, SiteConditions(), sizes=[12])
    used = sum(1 for m in d.inverters[0].mppts if m.strings)
    assert spd_plan(d, 5, 110).compulsory is False
    plan = spd_plan(d, 60, 110)  # built-in SPD covers the inverter end; over 10 m adds the array end
    assert (plan.inverter_end, plan.array_end) == (0, used)
    d = design_strings(panel, [mic], 24, SiteConditions(), sizes=[12])
    plan = spd_plan(d, 60, 110)
    assert (plan.inverter_end, plan.array_end) == (2, 2)
