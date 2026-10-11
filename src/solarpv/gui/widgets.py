"""Shared widgets: the roof grid view and small helpers."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QDoubleSpinBox, QGraphicsScene, QGraphicsView, QHeaderView, QMessageBox,
                               QSpinBox, QTableWidget, QTableWidgetItem, QWidget)

from ..layout import EMPTY, PANEL, SPECIAL, Grid

STRING_COLOURS = ["#F4B183", "#A9D18E", "#9DC3E6", "#FFD966", "#C9A0DC", "#8FD3C8", "#F8CBAD", "#B4C7E7",
                  "#E2EFDA", "#FCE4D6", "#D0CECE", "#BDD7EE", "#FFE699", "#C5E0B4", "#F4B084", "#DBDBDB"]
CELL = 22.0


class GridView(QGraphicsView):
    """Draws a layout grid. Click a cell to cycle empty → panel → special when editable."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.grid: Grid = []
        self.string_map: dict[tuple[int, int], str] = {}
        self.editable = False

    def set_grid(self, grid: Grid, string_map: dict | None = None, editable: bool = False) -> None:
        self.grid = grid
        self.string_map = string_map or {}
        self.editable = editable
        self.redraw()
        self.fit()

    def fit(self) -> None:
        if self.grid:
            self.fitInView(self.scene().itemsBoundingRect(), Qt.KeepAspectRatio)

    def showEvent(self, event) -> None:  # the view has its real size only once shown
        super().showEvent(event)
        self.fit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit()

    def redraw(self) -> None:
        scene = self.scene()
        scene.clear()
        colours: dict[str, QColor] = {}
        font = QFont()
        font.setPointSizeF(4.5)
        for r, row in enumerate(self.grid):
            for c, v in enumerate(row):
                rect = QRectF(c * CELL, r * CELL * 1.6, CELL - 2, CELL * 1.6 - 2)
                label = self.string_map.get((r, c))
                if v == PANEL:
                    if label:
                        colour = colours.setdefault(label, QColor(STRING_COLOURS[len(colours) % len(STRING_COLOURS)]))
                    else:
                        colour = QColor("#2E7D32")
                    pen = QPen(QColor("#1B3A1C"))
                elif v == SPECIAL:
                    colour, pen = QColor("#FFA726"), QPen(QColor("#E65100"))
                else:
                    colour, pen = QColor("#ECEFF1"), QPen(QColor("#CFD8DC"))
                item = scene.addRect(rect, pen, QBrush(colour))
                item.setData(0, (r, c))
                if label:
                    t = scene.addText(label.replace("-", "\n"), font)
                    t.setDefaultTextColor(QColor("#222"))
                    t.setPos(rect.x() - 1, rect.y() + 2)
        scene.setSceneRect(scene.itemsBoundingRect())

    def wheelEvent(self, event) -> None:  # zoom with the mouse wheel
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mousePressEvent(self, event) -> None:
        if self.editable and event.button() == Qt.LeftButton:
            pos = self.mapToScene(event.position().toPoint())
            c, r = int(pos.x() // CELL), int(pos.y() // (CELL * 1.6))
            if 0 <= r < len(self.grid) and 0 <= c < len(self.grid[0]):
                self.grid[r][c] = {EMPTY: PANEL, PANEL: SPECIAL, SPECIAL: EMPTY}[self.grid[r][c]]
                self.string_map = {}
                self.redraw()
                self.changed.emit()
                return
        super().mousePressEvent(event)


def spin(value: float, lo: float, hi: float, step: float = 1.0, decimals: int = 1, suffix: str = "") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(value)
    if suffix:
        s.setSuffix(suffix)
    return s


def ispin(value: int, lo: int, hi: int) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    return s


def table(headers: list[str], stretch: int | None = None) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    if stretch is not None:
        h.setSectionResizeMode(stretch, QHeaderView.Stretch)
    return t


def cell(value, editable: bool = False, colour: str | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem("" if value is None else (f"{value:g}" if isinstance(value, float) else str(value)))
    if not editable:
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    if colour:
        item.setBackground(QColor(colour))
    return item


def error(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)
