"""The main window's tabs: project, roofs, equipment, strings, BOM, checks."""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMessageBox, QPushButton, QSplitter, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..bom import typical_run_length
from ..layout import EMPTY, LayoutError, panel_count, read_layout, write_csv
from ..project import InverterChoice, Project, ProjectResult, Roof, estimate_route_m
from ..strings import SITE_PRESETS, SiteConditions, string_limits, suggest_inverters
from .dialogs import DxfDialog, RoofDimensionsDialog
from .widgets import GridView, cell, error, ispin, spin, table

STATUS_COLOURS = {"pass": "#C6EFCE", "warn": "#FFEB9C", "fail": "#FFC7CE", "info": "#DDEBF7"}


class ProjectTab(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.project: Project | None = None
        self.name, self.client, self.location, self.designer = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.preset = QComboBox()
        self.preset.addItem("Custom")
        self.preset.addItems(list(SITE_PRESETS))
        self.t_min = spin(18, -20, 40, 1, 1, " °C")
        self.t_max = spin(36, 10, 55, 1, 1, " °C")
        self.rise = spin(30, 10, 45, 1, 1, " °C")
        self.thunder = spin(100, 1, 250, 5, 0, " days/year")
        self.ol_min = spin(18.5, -50, 100, 0.5, 1, " %")
        self.ol_max = spin(31.5, -50, 100, 0.5, 1, " %")
        self.margin = spin(30, 0, 100, 5, 0, " %")
        self.drop = spin(10, 0, 100, 1, 1, " m")
        self.dc_drop = spin(3, 0.5, 5, 0.5, 1, " %")

        site = QGroupBox("Site")
        f = QFormLayout(site)
        f.addRow("Project name", self.name)
        f.addRow("Client", self.client)
        f.addRow("Location", self.location)
        f.addRow("Designer", self.designer)

        climate = QGroupBox("Climate (sets the string voltage limits)")
        f = QFormLayout(climate)
        f.addRow("Region", self.preset)
        f.addRow("Lowest ambient temperature", self.t_min)
        f.addRow("Highest ambient temperature", self.t_max)
        f.addRow("Cell temperature rise in full sun", self.rise)
        f.addRow("Thunder days (Annex A of the guideline)", self.thunder)

        rules = QGroupBox("Design rules")
        f = QFormLayout(rules)
        f.addRow("Minimum DC/AC overload", self.ol_min)
        f.addRow("Maximum DC/AC overload", self.ol_max)
        f.addRow("Maximum DC voltage drop", self.dc_drop)
        f.addRow("Cable allowance for routing and slack", self.margin)
        f.addRow("Height from roof to inverter", self.drop)

        layout = QHBoxLayout(self)
        col = QVBoxLayout()
        col.addWidget(site)
        col.addWidget(climate)
        col.addStretch()
        layout.addLayout(col, 1)
        col = QVBoxLayout()
        col.addWidget(rules)
        note = QLabel("Checks follow the PUCSL “Guidelines on Rooftop Solar PV Installation for Solar Service "
                      "Providers” (Rev 1, Sept 2022): DC ratings at 1.15 × Voc and 1.25 × Isc, string fuses, "
                      "DC drop under 3%, and surge protection against Lcrit.")
        note.setWordWrap(True)
        col.addWidget(note)
        col.addStretch()
        layout.addLayout(col, 1)

        self.preset.currentTextChanged.connect(self._preset)
        for w in (self.name, self.client, self.location, self.designer):
            w.editingFinished.connect(self._store)
        for w in (self.t_min, self.t_max, self.rise, self.thunder, self.ol_min, self.ol_max, self.margin,
                  self.drop, self.dc_drop):
            w.valueChanged.connect(self._store)

    def _preset(self, name: str) -> None:
        if name in SITE_PRESETS:
            s = SITE_PRESETS[name]
            self.t_min.setValue(s.min_ambient_c)
            self.t_max.setValue(s.max_ambient_c)
        self._store()

    def load(self, project: Project) -> None:
        self.project = None
        self.name.setText(project.name)
        self.client.setText(project.client)
        self.location.setText(project.location)
        self.designer.setText(project.designer)
        self.preset.setCurrentText(project.site_preset or "Custom")
        self.t_min.setValue(project.site.min_ambient_c)
        self.t_max.setValue(project.site.max_ambient_c)
        self.rise.setValue(project.site.cell_rise_c)
        self.thunder.setValue(project.thunder_days)
        self.ol_min.setValue(project.options.min_overload_pct)
        self.ol_max.setValue(project.options.max_overload_pct)
        self.margin.setValue(project.cable_margin_pct)
        self.drop.setValue(project.vertical_drop_m)
        self.dc_drop.setValue(project.max_dc_drop_pct)
        self.project = project

    def _store(self) -> None:
        p = self.project
        if p is None:
            return
        p.name, p.client = self.name.text(), self.client.text()
        p.location, p.designer = self.location.text(), self.designer.text()
        p.site_preset = self.preset.currentText() if self.preset.currentIndex() else ""
        p.site = SiteConditions(self.t_min.value(), self.t_max.value(), self.rise.value())
        p.thunder_days = self.thunder.value()
        p.options.min_overload_pct, p.options.max_overload_pct = self.ol_min.value(), self.ol_max.value()
        p.cable_margin_pct, p.vertical_drop_m = self.margin.value(), self.drop.value()
        p.max_dc_drop_pct = self.dc_drop.value()
        self.changed.emit()


class RoofsTab(QWidget):
    changed = Signal()

    def __init__(self, rules_getter, library_getter):
        super().__init__()
        self.rules_getter, self.library_getter = rules_getter, library_getter
        self.project: Project | None = None
        self.list = QListWidget()
        buttons = QVBoxLayout()
        for text, slot in [("Import CSV / Excel…", self.import_grid), ("Import AutoCAD DXF…", self.import_dxf),
                           ("From roof dimensions…", self.from_dimensions), ("Blank grid", self.blank),
                           ("Save grid as CSV…", self.save_csv), ("Remove roof", self.remove)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addStretch()
        left = QVBoxLayout()
        left.addWidget(QLabel("Roofs"))
        left.addWidget(self.list, 1)
        left.addLayout(buttons)

        self.name = QLineEdit()
        self.roof_type = QComboBox()
        self.auto_route = QCheckBox("Estimate")
        self.route = spin(50, 1, 2000, 5, 0, " m")
        self.blank_rows, self.blank_cols = ispin(10, 1, 500), ispin(10, 1, 500)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.view = GridView()
        self.view.changed.connect(self._grid_edited)
        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Roof type", self.roof_type)
        route = QHBoxLayout()
        route.addWidget(self.route)
        route.addWidget(self.auto_route)
        form.addRow("DC cable route to inverter (one way)", route)
        blank = QHBoxLayout()
        blank.addWidget(QLabel("Blank grid rows × columns"))
        blank.addWidget(self.blank_rows)
        blank.addWidget(self.blank_cols)
        right = QVBoxLayout()
        right.addLayout(form)
        right.addWidget(self.view, 1)
        right.addWidget(QLabel("Click a cell to switch empty → panel → special (2). Scroll to zoom, drag to pan."))
        right.addWidget(self.info)
        right.addLayout(blank)

        layout = QHBoxLayout(self)
        lw = QWidget()
        lw.setLayout(left)
        rw = QWidget()
        rw.setLayout(right)
        split = QSplitter()
        split.addWidget(lw)
        split.addWidget(rw)
        split.setStretchFactor(1, 1)
        layout.addWidget(split)

        self.list.currentRowChanged.connect(self.show_roof)
        self.name.editingFinished.connect(self._store)
        self.roof_type.currentIndexChanged.connect(self._store)
        self.route.valueChanged.connect(self._store)
        self.auto_route.toggled.connect(self._store)

    def refresh_roof_types(self) -> None:
        current = self.roof_type.currentData()
        self.roof_type.blockSignals(True)
        self.roof_type.clear()
        for key, rt in self.rules_getter().roof_types.items():
            self.roof_type.addItem(rt.label, key)
        if current:
            self.roof_type.setCurrentIndex(max(0, self.roof_type.findData(current)))
        self.roof_type.blockSignals(False)

    def load(self, project: Project) -> None:
        self.project = project
        self.refresh_roof_types()
        self.list.clear()
        for r in project.roofs:
            self.list.addItem(r.name)
        if project.roofs:
            self.list.setCurrentRow(0)
        else:
            self.view.set_grid([])
            self.info.setText("Add a roof: import a CSV/Excel grid or an AutoCAD DXF, or fit panels from "
                              "roof dimensions measured in Google Earth.")

    def current(self) -> Roof | None:
        i = self.list.currentRow()
        return self.project.roofs[i] if self.project and 0 <= i < len(self.project.roofs) else None

    def show_roof(self, _row: int = 0) -> None:
        roof = self.current()
        if roof is None:
            return
        for w in (self.name, self.roof_type, self.route, self.auto_route):
            w.blockSignals(True)
        self.name.setText(roof.name)
        self.roof_type.setCurrentIndex(max(0, self.roof_type.findData(roof.roof_type)))
        self.auto_route.setChecked(roof.dc_route_m is None)
        self.route.setEnabled(roof.dc_route_m is not None)
        if roof.dc_route_m is not None:
            self.route.setValue(roof.dc_route_m)
        for w in (self.name, self.roof_type, self.route, self.auto_route):
            w.blockSignals(False)
        self.view.set_grid(roof.grid, editable=True)
        self._info(roof)

    def _info(self, roof: Roof) -> None:
        run = typical_run_length(roof.grid)
        text = (f"{panel_count(roof.grid)} panels, grid {len(roof.grid)} rows × {len(roof.grid[0])} columns"
                + (f", typical run {run} panels" if run else "") + (f". Source: {roof.source}" if roof.source else ""))
        lib = self.library_getter()
        if roof.dc_route_m is None and self.project.panel_key:
            panel = lib.panel(self.project.panel_key)
            text += f". Estimated cable route {estimate_route_m(roof.grid, panel, self.project.vertical_drop_m):.0f} m."
        self.info.setText(text)

    def _store(self) -> None:
        roof = self.current()
        if roof is None:
            return
        roof.name = self.name.text() or roof.name
        self.list.currentItem().setText(roof.name)
        roof.roof_type = self.roof_type.currentData() or roof.roof_type
        self.route.setEnabled(not self.auto_route.isChecked())
        roof.dc_route_m = None if self.auto_route.isChecked() else self.route.value()
        self._info(roof)
        self.changed.emit()

    def _grid_edited(self) -> None:
        roof = self.current()
        if roof:
            self._info(roof)
            self.changed.emit()

    def _add(self, roof: Roof) -> None:
        if self.roof_type.currentData():
            roof.roof_type = self.roof_type.currentData()
        self.project.roofs.append(roof)
        self.list.addItem(roof.name)
        self.list.setCurrentRow(len(self.project.roofs) - 1)
        self.changed.emit()

    def import_grid(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Import layout grid", "", "Layouts (*.csv *.xlsx *.xlsm)")
        for path in paths:
            try:
                grid = read_layout(path)
            except (LayoutError, OSError) as exc:
                error(self, "Cannot read layout", f"{path}\n\n{exc}")
                continue
            name = path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
            self._add(Roof(name, grid, source=path))

    def import_dxf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import AutoCAD DXF", "", "AutoCAD DXF (*.dxf)")
        if not path:
            return
        try:
            dialog = DxfDialog(path, self)
            if not dialog.exec():
                return
            result = dialog.read()
        except Exception as exc:  # noqa: BLE001 - show any DXF problem to the user
            error(self, "Cannot read DXF", str(exc))
            return
        name = path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        self._add(Roof(name, result.grid, source=f"{path}: {result.source}"))
        if result.warnings:
            QMessageBox.information(self, "DXF import", "\n".join(result.warnings))

    def from_dimensions(self) -> None:
        lib = self.library_getter()
        panel = lib.panel(self.project.panel_key) if self.project.panel_key else None
        dialog = RoofDimensionsDialog(panel, self)
        if dialog.exec() and dialog.grid:
            d = dialog.dimensions()
            self._add(Roof(dialog.name.text() or "Roof", dialog.grid,
                           source=f"Google Earth: {d.length_m:g} × {d.width_m:g} m, slope {d.slope_deg:g}°"))

    def blank(self) -> None:
        grid = [[EMPTY] * self.blank_cols.value() for _ in range(self.blank_rows.value())]
        self._add(Roof(f"Roof {len(self.project.roofs) + 1}", grid, source="Drawn by hand"))

    def save_csv(self) -> None:
        roof = self.current()
        if roof is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save grid", f"{roof.name}.csv", "CSV (*.csv)")
        if path:
            write_csv(roof.grid, path)

    def remove(self) -> None:
        i = self.list.currentRow()
        if i < 0:
            return
        del self.project.roofs[i]
        self.list.takeItem(i)
        if not self.project.roofs:
            self.view.set_grid([])
        self.changed.emit()


class EquipmentTab(QWidget):
    changed = Signal()

    def __init__(self, library_getter):
        super().__init__()
        self.library_getter = library_getter
        self.project: Project | None = None
        self.panel = QComboBox()
        self.panel_info = QLabel()
        self.panel_info.setWordWrap(True)
        self.brand = QComboBox()
        self.model = QComboBox()
        self.qty = ispin(1, 1, 500)
        add = QPushButton("Add")
        add.clicked.connect(self.add_inverter)
        self.inverters = table(["Inverter", "Qty", "AC kW", "String length allowed"], stretch=0)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self.remove_inverter)
        suggest = QPushButton("Suggest inverters for this site")
        suggest.clicked.connect(self.suggest)
        self.suggestions = table(["Model", "Qty", "DC/AC overload"], stretch=0)
        self.suggestions.cellDoubleClicked.connect(self.use_suggestion)
        self.auto_sizes = QCheckBox("Choose string lengths automatically")
        self.follow_runs = QCheckBox("Follow the rail runs (string length = whole runs)")
        self.sizes = QLineEdit()
        self.sizes.setPlaceholderText("e.g. 12, 13")
        self.limits = QLabel()
        self.limits.setWordWrap(True)

        pbox = QGroupBox("Panel")
        f = QFormLayout(pbox)
        f.addRow("Module", self.panel)
        f.addRow(self.panel_info)

        ibox = QGroupBox("Inverters")
        v = QVBoxLayout(ibox)
        row = QHBoxLayout()
        row.addWidget(QLabel("Brand"))
        row.addWidget(self.brand)
        row.addWidget(self.model, 1)
        row.addWidget(QLabel("Qty"))
        row.addWidget(self.qty)
        row.addWidget(add)
        v.addLayout(row)
        v.addWidget(self.inverters)
        v.addWidget(remove)
        v.addWidget(suggest)
        v.addWidget(QLabel("Double-click a suggestion to use it:"))
        v.addWidget(self.suggestions)

        sbox = QGroupBox("String lengths")
        f = QFormLayout(sbox)
        f.addRow(self.auto_sizes)
        f.addRow(self.follow_runs)
        f.addRow("Fixed lengths (panels)", self.sizes)
        f.addRow(self.limits)

        layout = QHBoxLayout(self)
        col = QVBoxLayout()
        col.addWidget(pbox)
        col.addWidget(sbox)
        col.addStretch()
        layout.addLayout(col, 1)
        layout.addWidget(ibox, 2)

        self.panel.currentIndexChanged.connect(self._store)
        self.brand.currentTextChanged.connect(self._fill_models)
        self.auto_sizes.toggled.connect(self._store)
        self.follow_runs.toggled.connect(self._store)
        self.sizes.editingFinished.connect(self._store)

    def refresh_library(self) -> None:
        lib = self.library_getter()
        self.panel.blockSignals(True)
        self.panel.clear()
        for p in lib.panels:
            self.panel.addItem(f"{p.brand} {p.model} ({p.pmax_w:g} W)", p.key)
        self.panel.blockSignals(False)
        brands = sorted({i.brand for i in lib.inverters})
        self.brand.blockSignals(True)
        self.brand.clear()
        self.brand.addItems(brands)
        self.brand.blockSignals(False)
        self._fill_models(self.brand.currentText())

    def _fill_models(self, brand: str) -> None:
        self.model.clear()
        for i in self.library_getter().inverters:
            if i.brand == brand:
                self.model.addItem(f"{i.model} ({i.ac_kw:g} kW)", i.key)

    def load(self, project: Project) -> None:
        self.project = None
        self.refresh_library()
        lib = self.library_getter()
        if not project.panel_key and lib.panels:
            project.panel_key = lib.panels[0].key
        self.panel.setCurrentIndex(max(0, self.panel.findData(project.panel_key)))
        brand = next((i.brand for c in project.inverters for i in lib.inverters if i.key == c.key), "SolaX")
        if self.brand.findText(brand) >= 0:
            self.brand.setCurrentText(brand)
        self.auto_sizes.setChecked(not project.string_sizes)
        self.follow_runs.setChecked(project.follow_runs)
        self.sizes.setText(", ".join(map(str, project.string_sizes)))
        self.project = project
        self._show()

    def _show(self) -> None:
        p = self.project
        lib = self.library_getter()
        try:
            panel = lib.panel(p.panel_key)
        except KeyError:
            panel = None
        if panel:
            self.panel_info.setText(f"Pmax {panel.pmax_w:g} W, Voc {panel.voc_v} V, Vmp {panel.vmp_v} V, Isc {panel.isc_a} A, "
                                    f"Imp {panel.imp_a} A, Voc coeff {panel.temp_coeff_voc_pct}%/°C, "
                                    f"{panel.length_m} × {panel.width_m} m. {panel.source}")
        self.inverters.setRowCount(0)
        lines = []
        for choice in p.inverters:
            try:
                inv = lib.inverter(choice.key)
            except KeyError:
                continue
            lim = string_limits(panel, inv, p.site) if panel else None
            r = self.inverters.rowCount()
            self.inverters.insertRow(r)
            self.inverters.setItem(r, 0, cell(choice.key))
            self.inverters.setItem(r, 1, cell(choice.qty))
            self.inverters.setItem(r, 2, cell(inv.ac_kw))
            self.inverters.setItem(r, 3, cell(f"{lim.min_panels} to {lim.max_panels} panels" if lim else ""))
            if lim:
                lines.append(f"{inv.model}: " + "; ".join(lim.reasons))
        total_ac = sum(lib.inverter(c.key).ac_kw * c.qty for c in p.inverters if c.key in {i.key for i in lib.inverters})
        kwp = p.panels * panel.pmax_w / 1000 if panel else 0
        head = f"{p.panels} panels = {kwp:.2f} kWp; inverters {total_ac:g} kW AC"
        if total_ac:
            head += f" ({(kwp / total_ac - 1) * 100:.1f}% overload)"
        self.limits.setText(head + "\n" + "\n".join(lines))
        self.sizes.setEnabled(not self.auto_sizes.isChecked())
        self.follow_runs.setEnabled(self.auto_sizes.isChecked())

    def _store(self) -> None:
        p = self.project
        if p is None:
            return
        p.panel_key = self.panel.currentData() or p.panel_key
        p.follow_runs = self.follow_runs.isChecked()
        if self.auto_sizes.isChecked():
            p.string_sizes = []
        else:
            try:
                p.string_sizes = sorted({int(x) for x in self.sizes.text().replace(";", ",").split(",") if x.strip()})
            except ValueError:
                error(self, "String lengths", "Enter whole numbers separated by commas, e.g. 12, 13")
        self._show()
        self.changed.emit()

    def add_inverter(self) -> None:
        key = self.model.currentData()
        if not key:
            return
        for c in self.project.inverters:
            if c.key == key:
                c.qty += self.qty.value()
                break
        else:
            self.project.inverters.append(InverterChoice(key, self.qty.value()))
        self._show()
        self.changed.emit()

    def remove_inverter(self) -> None:
        rows = sorted({i.row() for i in self.inverters.selectedIndexes()}, reverse=True)
        for r in rows:
            key = self.inverters.item(r, 0).text()
            self.project.inverters = [c for c in self.project.inverters if c.key != key]
        self._show()
        self.changed.emit()

    def suggest(self) -> None:
        lib = self.library_getter()
        if not self.project.panel_key or not self.project.panels:
            error(self, "Suggest inverters", "Choose a panel and add a roof first.")
            return
        models = [i for i in lib.inverters if i.brand == self.brand.currentText()]
        found = suggest_inverters(lib.panel(self.project.panel_key), self.project.panels, models, self.project.options)
        self.suggestions.setRowCount(0)
        for inv, qty, ol in found[:12]:
            r = self.suggestions.rowCount()
            self.suggestions.insertRow(r)
            item = cell(inv.key)
            item.setData(Qt.UserRole, (inv.key, qty))
            self.suggestions.setItem(r, 0, item)
            self.suggestions.setItem(r, 1, cell(qty))
            self.suggestions.setItem(r, 2, cell(f"{ol:.1f}%"))
        if not found:
            error(self, "Suggest inverters", "No single model from this brand fits the overload window.")

    def use_suggestion(self, row: int, _col: int) -> None:
        key, qty = self.suggestions.item(row, 0).data(Qt.UserRole)
        self.project.inverters = [InverterChoice(key, qty)]
        self._show()
        self.changed.emit()


class StringsTab(QWidget):
    def __init__(self):
        super().__init__()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.inverters = table(["Inverter", "Model", "Strings", "Panels", "DC kWp", "Overload", "MPPTs"], stretch=6)
        self.schedule = table(["String", "Inverter", "MPPT", "Panels", "Roof"])
        self.roof = QComboBox()
        self.view = GridView()
        self.result: ProjectResult | None = None
        self.roof.currentIndexChanged.connect(self._show_roof)
        left = QVBoxLayout()
        left.addWidget(self.summary)
        left.addWidget(self.inverters, 1)
        left.addWidget(self.schedule, 1)
        right = QVBoxLayout()
        right.addWidget(QLabel("Suggested string routing (follows the rail runs; adjust on site):"))
        right.addWidget(self.roof)
        right.addWidget(self.view, 1)
        layout = QHBoxLayout(self)
        lw, rw = QWidget(), QWidget()
        lw.setLayout(left)
        rw.setLayout(right)
        split = QSplitter()
        split.addWidget(lw)
        split.addWidget(rw)
        layout.addWidget(split)

    def show_result(self, result: ProjectResult) -> None:
        self.result = result
        d, p = result.design, result.panel
        self.inverters.setRowCount(0)
        self.schedule.setRowCount(0)
        if d is None:
            self.summary.setText(f"<b>No string design yet:</b> {result.design_error}")
            self.roof.clear()
            self.view.set_grid([])
            return
        sizes = ", ".join(f"{c} strings of {s}" for s, c in sorted(d.combination.items()))
        text = (f"<b>{d.panels_used} panels, {d.dc_kwp:.2f} kWp DC on {d.ac_kw:g} kW AC "
                f"({(d.dc_kwp / d.ac_kw - 1) * 100:.1f}% overload).</b> {d.total_strings} strings: {sizes}.")
        if d.warnings:
            text += "<br><span style='color:#9C5700'>⚠ " + "<br>⚠ ".join(d.warnings) + "</span>"
        self.summary.setText(text)
        for a in d.inverters:
            r = self.inverters.rowCount()
            self.inverters.insertRow(r)
            ol = a.overload_pct(p)
            ok = result.design and not any(w.startswith(f"INV{a.number:02d}") for w in d.warnings)
            vals = [f"INV{a.number:02d}", a.inverter.key, a.strings, a.panels, round(a.dc_kw(p), 2), f"{ol:.1f}%",
                    "; ".join(f"MPPT{m.index}: {m.strings}×{m.string_size}" for m in a.mppts if m.strings)]
            for c, v in enumerate(vals):
                self.inverters.setItem(r, c, cell(v, colour=None if ok else "#FFEB9C"))
        roof_of = {}
        for rr in result.roofs:
            for label in rr.string_map.values():
                roof_of.setdefault(label, rr.roof.name)
        for s in d.schedule():
            r = self.schedule.rowCount()
            self.schedule.insertRow(r)
            for c, v in enumerate([s["label"], f"INV{s['inverter']:02d}", s["mppt"], s["panels"],
                                   roof_of.get(s["label"], "")]):
                self.schedule.setItem(r, c, cell(v))
        self.roof.blockSignals(True)
        self.roof.clear()
        self.roof.addItems([rr.roof.name for rr in result.roofs])
        self.roof.blockSignals(False)
        self._show_roof()

    def _show_roof(self) -> None:
        if not self.result:
            return
        i = self.roof.currentIndex()
        if 0 <= i < len(self.result.roofs):
            rr = self.result.roofs[i]
            self.view.set_grid(copy.deepcopy(rr.roof.grid), rr.string_map)


class BomTab(QWidget):
    changed = Signal()
    export_requested = Signal()

    def __init__(self):
        super().__init__()
        self.project: Project | None = None
        self.table = table(["Category", "Code", "Description", "Unit", "Qty", "Notes"], stretch=2)
        self.table.itemChanged.connect(self._edited)
        self.errors = QLabel()
        self.errors.setWordWrap(True)
        self.errors.setStyleSheet("color: #9C0006")
        export = QPushButton("Export Excel report…")
        export.clicked.connect(self.export_requested.emit)
        top = QHBoxLayout()
        top.addWidget(QLabel("Yellow quantities are entered by hand: double-click to type a number. "
                             "Edit the formulas in the BOM Rules tab."))
        top.addStretch()
        top.addWidget(export)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.errors)
        self._loading = False

    def show_result(self, project: Project, result: ProjectResult) -> None:
        self.project = project
        self._loading = True
        self.table.setRowCount(0)
        for line in result.bom.lines:
            r = self.table.rowCount()
            self.table.insertRow(r)
            manual = line.manual or line.description in project.manual_quantities
            colour = "#FFF2CC" if manual else None
            for c, v in enumerate([line.category, line.code, line.description, line.unit]):
                self.table.setItem(r, c, cell(v, colour=colour))
            self.table.setItem(r, 4, cell(line.qty, editable=manual, colour=colour))
            self.table.setItem(r, 5, cell(line.note, colour=colour))
        self.errors.setText("\n".join(result.bom.errors))
        self._loading = False

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 4 or self.project is None:
            return
        desc = self.table.item(item.row(), 2).text()
        text = item.text().strip()
        if not text:
            self.project.manual_quantities.pop(desc, None)
        else:
            try:
                self.project.manual_quantities[desc] = float(text)
            except ValueError:
                error(self, "Quantity", "Enter a number")
                return
        self.changed.emit()


class ChecksTab(QWidget):
    def __init__(self):
        super().__init__()
        self.table = table(["Check", "Result", "Detail", "Reference"], stretch=2)
        self.table.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Design checks against the inverter datasheets and the PUCSL rooftop solar guideline."))
        layout.addWidget(self.table)

    def show_result(self, result: ProjectResult) -> None:
        self.table.setRowCount(0)
        for ch in result.checks:
            r = self.table.rowCount()
            self.table.insertRow(r)
            colour = STATUS_COLOURS[ch.status]
            for c, v in enumerate([ch.title, ch.status.upper(), ch.detail, ch.reference]):
                self.table.setItem(r, c, cell(v, colour=colour if c == 1 else None))
        self.table.resizeRowsToContents()

