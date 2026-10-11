"""Dialogs for creating roof layouts."""

from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QVBoxLayout)

from ..layout import LayoutError, RoofDimensions, grid_from_dimensions, panel_count
from ..library import Panel
from .widgets import GridView, ispin, spin


class RoofDimensionsDialog(QDialog):
    """Fit panels into a roof face measured in Google Earth."""

    def __init__(self, panel: Panel | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Roof from dimensions (Google Earth)")
        self.resize(900, 560)
        self.grid = None
        form = QFormLayout()
        self.name = QLineEdit("Roof")
        self.length = spin(40, 1, 1000, 0.5, 2, " m")
        self.width = spin(20, 1, 1000, 0.5, 2, " m")
        self.slope = spin(0, 0, 60, 1, 1, " °")
        self.setback = spin(0.5, 0, 10, 0.1, 2, " m")
        self.p_len = spin(panel.length_m if panel else 2.333, 0.5, 3, 0.001, 3, " m")
        self.p_wid = spin(panel.width_m if panel else 1.134, 0.5, 2, 0.001, 3, " m")
        self.portrait = QComboBox()
        self.portrait.addItems(["Portrait (long side along the rails)", "Landscape"])
        self.gap = spin(0.02, 0, 0.5, 0.01, 3, " m")
        self.max_run = ispin(0, 0, 100)
        self.run_break = spin(0.6, 0, 3, 0.1, 2, " m")
        self.walk_every = ispin(0, 0, 100)
        self.walk_width = spin(0.6, 0.2, 3, 0.1, 2, " m")
        hint = QLabel("Measure the roof face in Google Earth Pro with the ruler (Path) tool. "
                      "Length runs along the rails, down the slope; width runs across. "
                      "Google Earth shows plan view, so enter the slope to get the true length.")
        hint.setWordWrap(True)
        form.addRow(hint)
        form.addRow("Roof name", self.name)
        form.addRow("Length along rails (plan)", self.length)
        form.addRow("Width across roof", self.width)
        form.addRow("Roof slope", self.slope)
        form.addRow("Edge setback", self.setback)
        form.addRow("Panel length", self.p_len)
        form.addRow("Panel width", self.p_wid)
        form.addRow("Orientation", self.portrait)
        form.addRow("Gap between panels", self.gap)
        form.addRow("Break runs after (panels, 0 = never)", self.max_run)
        form.addRow("Run break width", self.run_break)
        form.addRow("Walkway after every (columns, 0 = none)", self.walk_every)
        form.addRow("Walkway width", self.walk_width)
        self.status = QLabel()
        self.view = GridView()
        for w in (self.length, self.width, self.slope, self.setback, self.p_len, self.p_wid, self.gap,
                  self.max_run, self.run_break, self.walk_every, self.walk_width):
            w.valueChanged.connect(self.update_preview)
        self.portrait.currentIndexChanged.connect(self.update_preview)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        right = QVBoxLayout()
        right.addWidget(self.view, 1)
        right.addWidget(self.status)
        top = QHBoxLayout()
        top.addLayout(form)
        top.addLayout(right, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(buttons)
        self.update_preview()

    def dimensions(self) -> RoofDimensions:
        return RoofDimensions(
            length_m=self.length.value(), width_m=self.width.value(), edge_setback_m=self.setback.value(),
            panel_length_m=self.p_len.value(), panel_width_m=self.p_wid.value(),
            portrait=self.portrait.currentIndex() == 0, gap_m=self.gap.value(), max_run_panels=self.max_run.value(),
            run_break_m=self.run_break.value(), walkway_every_cols=self.walk_every.value(),
            walkway_width_m=self.walk_width.value(), slope_deg=self.slope.value())

    def update_preview(self) -> None:
        try:
            self.grid = grid_from_dimensions(self.dimensions())
        except LayoutError as exc:
            self.grid = None
            self.status.setText(str(exc))
            self.view.set_grid([])
            return
        d = self.dimensions()
        self.status.setText(f"{panel_count(self.grid)} panels in {len(self.grid[0])} columns × {len(self.grid)} rows. "
                            f"Usable area {d.along_rail_m():.1f} × {d.across_m():.1f} m.")
        self.view.set_grid(self.grid)


class DxfDialog(QDialog):
    """Pick which block in an AutoCAD DXF is the panel."""

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        from ..dxf_import import block_names

        self.setWindowTitle("Import from AutoCAD DXF")
        self.path = path
        self.block = QComboBox()
        counts = block_names(path)
        self.block.addItem("(rectangles: closed polylines)", None)
        for name, n in counts.most_common():
            self.block.addItem(f"{name}  ({n} copies)", name)
        if counts:
            self.block.setCurrentIndex(1)
        self.layer = QLineEdit()
        self.layer.setPlaceholderText("any layer")
        form = QFormLayout()
        form.addRow(QLabel("Panels are read from copies of one block, or from closed rectangles of the panel size. "
                           "Each column of panels becomes one rail run."))
        form.addRow("Panel block", self.block)
        form.addRow("Layer", self.layer)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def read(self):
        from ..dxf_import import grid_from_dxf

        block = self.block.currentData()
        layer = self.layer.text().strip() or None
        return grid_from_dxf(self.path, block=block, layer=layer, rectangles=block is None)
