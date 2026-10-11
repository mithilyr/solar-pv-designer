"""Panel and inverter libraries.

The built-in library ships inside the package (``solarpv/data``). Engineers can
add or edit entries; their copy is saved in the user data folder and takes
precedence over the built-in one, so an app update never overwrites their edits.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from importlib import resources
from pathlib import Path


def user_data_dir() -> Path:
    """Per-user folder for the editable library, rules and settings."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = base / "SolarPVDesigner"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Panel:
    brand: str
    model: str
    pmax_w: float
    voc_v: float
    vmp_v: float
    isc_a: float
    imp_a: float
    temp_coeff_voc_pct: float  # %/°C, negative
    temp_coeff_isc_pct: float = 0.05  # %/°C, positive
    temp_coeff_pmax_pct: float = -0.35
    length_m: float = 2.278
    width_m: float = 1.134
    max_series_fuse_a: float = 25.0
    max_system_voltage_v: float = 1500.0
    source: str = ""

    @property
    def key(self) -> str:
        return f"{self.brand} {self.model}"


@dataclass
class Inverter:
    brand: str
    model: str
    ac_kw: float
    max_dc_voltage: float
    mppt_min_v: float
    mppt_max_v: float
    mppts: int
    strings_per_mppt: list[int]
    max_current_per_mppt: list[float]
    max_isc_per_mppt: list[float]
    phase: int = 3
    ac_voltage_v: float = 400.0  # line-to-line (230 for single phase)
    max_pv_kwp: float | None = None
    start_voltage: float | None = None
    source: str = ""

    @property
    def key(self) -> str:
        return f"{self.brand} {self.model}"

    @property
    def max_strings(self) -> int:
        return sum(self.strings_per_mppt)

    def validate(self) -> list[str]:
        problems = []
        for name in ("strings_per_mppt", "max_current_per_mppt", "max_isc_per_mppt"):
            if len(getattr(self, name)) != self.mppts:
                problems.append(f"{name} must have one value per MPPT ({self.mppts})")
        if self.mppt_min_v >= self.mppt_max_v:
            problems.append("MPPT minimum voltage must be below the maximum")
        if self.mppt_max_v > self.max_dc_voltage:
            problems.append("MPPT maximum voltage exceeds max DC input voltage")
        return problems


def _from_dict(cls, data: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class Library:
    panels: list[Panel] = field(default_factory=list)
    inverters: list[Inverter] = field(default_factory=list)

    def panel(self, key: str) -> Panel:
        for p in self.panels:
            if p.key == key:
                return p
        raise KeyError(f"Panel not in library: {key}")

    def inverter(self, key: str) -> Inverter:
        for i in self.inverters:
            if i.key == key:
                return i
        raise KeyError(f"Inverter not in library: {key}")

    def upsert_panel(self, panel: Panel) -> None:
        self.panels = [p for p in self.panels if p.key != panel.key] + [panel]

    def upsert_inverter(self, inverter: Inverter) -> None:
        self.inverters = [i for i in self.inverters if i.key != inverter.key] + [inverter]

    def to_files(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "panels.json").write_text(
            json.dumps({"version": 1, "panels": [asdict(p) for p in self.panels]}, indent=1), "utf-8")
        (folder / "inverters.json").write_text(
            json.dumps({"version": 1, "inverters": [asdict(i) for i in self.inverters]}, indent=1), "utf-8")


def _read_json(name: str, folder: Path | None) -> dict:
    if folder is not None and (folder / name).exists():
        return json.loads((folder / name).read_text("utf-8"))
    return json.loads(resources.files("solarpv.data").joinpath(name).read_text("utf-8"))


def load_library(folder: Path | None = None, use_user_copy: bool = True) -> Library:
    """Load the library: the user's edited copy if there is one, else the built-in."""
    if folder is None and use_user_copy:
        folder = user_data_dir() / "library"
    panels = [_from_dict(Panel, d) for d in _read_json("panels.json", folder)["panels"]]
    inverters = [_from_dict(Inverter, d) for d in _read_json("inverters.json", folder)["inverters"]]
    return Library(panels, inverters)


def save_user_library(lib: Library) -> Path:
    folder = user_data_dir() / "library"
    lib.to_files(folder)
    return folder
