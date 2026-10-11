"""Editors for the panel/inverter library and the BOM rules."""

from __future__ import annotations

import dataclasses
import json

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabWidget,
                               QTableWidget, QVBoxLayout, QWidget)

from ..bom import FEATURE_HELP, BomItem, BomRules, load_rules, save_user_rules
from ..formula import FormulaError, evaluate, names_used
from ..library import Inverter, Library, Panel, load_library, save_user_library
from .widgets import cell, error, table

PANEL_FIELDS = [f.name for f in dataclasses.fields(Panel)]
INVERTER_FIELDS = [f.name for f in dataclasses.fields(Inverter)]
LIST_FIELDS = {"strings_per_mppt", "max_current_per_mppt", "max_isc_per_mppt"}


def _to_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list):
        return ", ".join(f"{x:g}" if isinstance(x, float) else str(x) for x in v)
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _parse(cls, name: str, text: str):
    ftype = {f.name: f.type for f in dataclasses.fields(cls)}[name]
    text = text.strip()
    if name in LIST_FIELDS:
        conv = int if name == "strings_per_mppt" else float
        return [conv(x) for x in text.replace(";", ",").split(",") if x.strip()]
    if "bool" in str(ftype):
        return text.lower() in ("yes", "y", "true", "1")
    if "float" in str(ftype):
        if not text and "None" in str(ftype):
            return None
        return float(text)
    if "int" in str(ftype):
        return int(float(text))
    return text


class _RecordTable(QWidget):
    def __init__(self, cls, fields: list[str], help_text: str):
        super().__init__()
        self.cls, self.fields = cls, fields
        self.table: QTableWidget = table(fields)
        add = QPushButton("Add row")
        add.clicked.connect(self.add_row)
        dup = QPushButton("Duplicate selected")
        dup.clicked.connect(self.duplicate)
        remove = QPushButton("Delete selected")
        remove.clicked.connect(self.remove)
        buttons = QHBoxLayout()
        for b in (add, dup, remove):
            buttons.addWidget(b)
        buttons.addStretch()
        label = QLabel(help_text)
        label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(label)
        layout.addLayout(buttons)
        layout.addWidget(self.table)

    def load(self, records: list) -> None:
        self.table.setRowCount(0)
        for rec in records:
            self._append([_to_text(getattr(rec, f)) for f in self.fields])

    def _append(self, values: list[str]) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, v in enumerate(values):
            self.table.setItem(r, c, cell(v, editable=True))

    def add_row(self) -> None:
        self._append([""] * len(self.fields))

    def duplicate(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}):
            values = [self.table.item(r, c).text() if self.table.item(r, c) else "" for c in range(len(self.fields))]
            values[self.fields.index("model")] += " (copy)"
            self._append(values)

    def remove(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def records(self) -> list:
        out = []
        for r in range(self.table.rowCount()):
            raw = {f: (self.table.item(r, c).text() if self.table.item(r, c) else "") for c, f in enumerate(self.fields)}
            if not raw["model"].strip():
                continue
            try:
                kwargs = {f: _parse(self.cls, f, v) for f, v in raw.items() if v.strip() or f in ("source",)}
                rec = self.cls(**kwargs)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Row {r + 1} ({raw['brand']} {raw['model']}): {exc}") from exc
            if isinstance(rec, Inverter) and rec.validate():
                raise ValueError(f"Row {r + 1} ({rec.key}): " + "; ".join(rec.validate()))
            out.append(rec)
        return out


class LibraryTab(QWidget):
    changed = Signal()

    def __init__(self, library: Library):
        super().__init__()
        self.library = library
        self.panels = _RecordTable(Panel, PANEL_FIELDS,
                                   "Panel datasheet values at STC. Temperature coefficients in %/°C (Voc negative). "
                                   "max_series_fuse_a is on the datasheet as 'maximum series fuse rating'.")
        self.inverters = _RecordTable(Inverter, INVERTER_FIELDS,
                                      "One value per MPPT in the list columns, separated by commas (e.g. 2, 2, 2). "
                                      "max_pv_kwp is the datasheet's 'max recommended PV array power'; leave blank if not given. "
                                      "dc_spd_builtin: yes if the datasheet lists a Type II DC surge arrester.")
        tabs = QTabWidget()
        tabs.addTab(self.inverters, "Inverters")
        tabs.addTab(self.panels, "Panels")
        save = QPushButton("Save library")
        save.clicked.connect(self.save)
        reset = QPushButton("Reset to built-in")
        reset.clicked.connect(self.reset)
        export = QPushButton("Export…")
        export.clicked.connect(self.export)
        imp = QPushButton("Import…")
        imp.clicked.connect(self.import_)
        buttons = QHBoxLayout()
        buttons.addWidget(QLabel("Your edits are saved for your Windows user and survive app updates. "
                                 "Use Export/Import to share a company library."))
        buttons.addStretch()
        for b in (imp, export, reset, save):
            buttons.addWidget(b)
        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(tabs)
        self.show_library()

    def show_library(self) -> None:
        self.panels.load(self.library.panels)
        self.inverters.load(self.library.inverters)

    def _collect(self) -> Library | None:
        try:
            return Library(self.panels.records(), self.inverters.records())
        except ValueError as exc:
            error(self, "Library", str(exc))
            return None

    def save(self) -> None:
        lib = self._collect()
        if lib is None:
            return
        self.library.panels, self.library.inverters = lib.panels, lib.inverters
        folder = save_user_library(self.library)
        QMessageBox.information(self, "Library", f"Saved to {folder}")
        self.changed.emit()

    def reset(self) -> None:
        if QMessageBox.question(self, "Library", "Replace your library with the built-in one?") != QMessageBox.Yes:
            return
        lib = load_library(use_user_copy=False)
        self.library.panels, self.library.inverters = lib.panels, lib.inverters
        save_user_library(self.library)
        self.show_library()
        self.changed.emit()

    def export(self) -> None:
        lib = self._collect()
        if lib is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export library", "solar-library.json", "JSON (*.json)")
        if path:
            data = {"panels": [dataclasses.asdict(p) for p in lib.panels],
                    "inverters": [dataclasses.asdict(i) for i in lib.inverters]}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)

    def import_(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import library", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for d in data.get("panels", []):
                self.library.upsert_panel(Panel(**d))
            for d in data.get("inverters", []):
                self.library.upsert_inverter(Inverter(**d))
        except (OSError, TypeError, ValueError) as exc:
            error(self, "Import library", str(exc))
            return
        self.show_library()
        QMessageBox.information(self, "Library", "Imported. Press Save library to keep it.")


ITEM_FIELDS = ["category", "code", "description", "unit", "qty", "round", "note"]
COMMON = "__common__"


class RulesTab(QWidget):
    changed = Signal()

    def __init__(self, rules: BomRules):
        super().__init__()
        self.rules = rules
        self.which = QComboBox()
        self.variables = table(["Name", "Formula"], stretch=1)
        self.items = table(["Category", "Code", "Description", "Unit", "Qty formula", "Rounding", "Note"], stretch=2)
        names = "<br>".join(f"<b>{k}</b>: {v}" for k, v in FEATURE_HELP.items())
        help_label = QLabel("Quantities are formulas, e.g. <code>2 * (panels - runs)</code> or "
                            "<code>ceil(rail_bases / 20)</code>. Leave a formula blank for a line entered by hand. "
                            "Rounding: ceil (round up), round, or none. {panel_model} in a description is replaced "
                            "by the chosen panel.<br><br><b>Names you can use</b> (plus the variables above):<br>" + names)
        help_label.setWordWrap(True)
        add_var, del_var = QPushButton("Add variable"), QPushButton("Delete variable")
        add_item, del_item = QPushButton("Add item"), QPushButton("Delete item")
        add_var.clicked.connect(lambda: self._add(self.variables, 2))
        del_var.clicked.connect(lambda: self._delete(self.variables))
        add_item.clicked.connect(lambda: self._add(self.items, len(ITEM_FIELDS)))
        del_item.clicked.connect(lambda: self._delete(self.items))
        save, reset = QPushButton("Check and save rules"), QPushButton("Reset to built-in")
        save.clicked.connect(self.save)
        reset.clicked.connect(self.reset)

        top = QHBoxLayout()
        top.addWidget(QLabel("Rules for"))
        top.addWidget(self.which, 1)
        top.addWidget(reset)
        top.addWidget(save)
        vbtn = QHBoxLayout()
        vbtn.addWidget(add_var)
        vbtn.addWidget(del_var)
        vbtn.addStretch()
        ibtn = QHBoxLayout()
        ibtn.addWidget(add_item)
        ibtn.addWidget(del_item)
        ibtn.addStretch()
        left = QVBoxLayout()
        left.addLayout(top)
        left.addWidget(QLabel("Variables (worked out first, in order)"))
        left.addWidget(self.variables, 1)
        left.addLayout(vbtn)
        left.addWidget(QLabel("Items"))
        left.addWidget(self.items, 3)
        left.addLayout(ibtn)
        layout = QHBoxLayout(self)
        layout.addLayout(left, 3)
        layout.addWidget(help_label, 1)
        self._current = None
        self.which.currentIndexChanged.connect(self._switch)
        self._fill_which()

    def _fill_which(self) -> None:
        self.which.blockSignals(True)
        self.which.clear()
        self.which.addItem("Common items (every site)", COMMON)
        for key, rt in self.rules.roof_types.items():
            self.which.addItem(f"Roof type: {rt.label}", key)
        self.which.blockSignals(False)
        self._current = None
        self._switch()

    def _switch(self) -> None:
        if self._current is not None:
            self._store(self._current)
        key = self.which.currentData()
        self._current = key
        if key == COMMON:
            variables, items = {}, self.rules.common_items
            self.variables.setEnabled(False)
        else:
            rt = self.rules.roof_types[key]
            variables, items = rt.variables, rt.items
            self.variables.setEnabled(True)
        self.variables.setRowCount(0)
        for name, expr in variables.items():
            r = self.variables.rowCount()
            self.variables.insertRow(r)
            self.variables.setItem(r, 0, cell(name, True))
            self.variables.setItem(r, 1, cell(expr, True))
        self.items.setRowCount(0)
        for it in items:
            r = self.items.rowCount()
            self.items.insertRow(r)
            for c, f in enumerate(ITEM_FIELDS):
                self.items.setItem(r, c, cell(getattr(it, f), True))

    def _text(self, t: QTableWidget, r: int, c: int) -> str:
        item = t.item(r, c)
        return item.text().strip() if item else ""

    def _store(self, key) -> None:
        items = []
        for r in range(self.items.rowCount()):
            values = {f: self._text(self.items, r, c) for c, f in enumerate(ITEM_FIELDS)}
            if not values["description"]:
                continue
            values["round"] = values["round"] or "ceil"
            values["unit"] = values["unit"] or "EA"
            items.append(BomItem(**values))
        if key == COMMON:
            self.rules.common_items = items
        else:
            rt = self.rules.roof_types[key]
            rt.items = items
            rt.variables = {self._text(self.variables, r, 0): self._text(self.variables, r, 1)
                            for r in range(self.variables.rowCount()) if self._text(self.variables, r, 0)}

    def _add(self, t: QTableWidget, n: int) -> None:
        r = t.rowCount()
        t.insertRow(r)
        for c in range(n):
            t.setItem(r, c, cell("", True))

    def _delete(self, t: QTableWidget) -> None:
        for r in sorted({i.row() for i in t.selectedIndexes()}, reverse=True):
            t.removeRow(r)

    def validate(self) -> list[str]:
        """Evaluate every formula with sample numbers to catch typos before saving."""
        sample = {k: 10.0 for k in FEATURE_HELP}
        problems = []
        for key, rt in self.rules.roof_types.items():
            names = dict(sample)
            for var, expr in rt.variables.items():
                try:
                    names[var] = float(evaluate(expr, names))
                except (FormulaError, ZeroDivisionError, TypeError) as exc:
                    problems.append(f"{rt.label}: variable {var}: {exc}")
                    names[var] = 1.0
            for it in rt.items:
                if it.qty:
                    try:
                        evaluate(it.qty, names)
                    except (FormulaError, ZeroDivisionError, TypeError) as exc:
                        problems.append(f"{rt.label}: {it.description}: {exc}")
        for it in self.rules.common_items:
            if it.qty:
                unknown = names_used(it.qty) - set(sample)
                if unknown:
                    problems.append(f"Common: {it.description}: only site names allowed, not {', '.join(sorted(unknown))}")
                    continue
                try:
                    evaluate(it.qty, sample)
                except (FormulaError, ZeroDivisionError, TypeError) as exc:
                    problems.append(f"Common: {it.description}: {exc}")
        return problems

    def save(self) -> None:
        self._store(self._current)
        problems = self.validate()
        if problems:
            error(self, "BOM rules", "Fix these formulas first:\n\n" + "\n".join(problems[:20]))
            return
        path = save_user_rules(self.rules)
        QMessageBox.information(self, "BOM rules", f"Saved to {path}")
        self.changed.emit()

    def reset(self) -> None:
        if QMessageBox.question(self, "BOM rules", "Replace your rules with the built-in ones?") != QMessageBox.Yes:
            return
        fresh = load_rules(use_user_copy=False)
        self.rules.common_items, self.rules.roof_types = fresh.common_items, fresh.roof_types
        save_user_rules(self.rules)
        self._fill_which()
        self.changed.emit()
