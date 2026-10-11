"""Main window."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMainWindow, QMessageBox, QTabWidget

from .. import __version__
from ..bom import load_rules
from ..export_excel import export_report
from ..library import load_library
from ..project import PROJECT_SUFFIX, Project, compute
from .editors import LibraryTab, RulesTab
from .tabs import BomTab, ChecksTab, EquipmentTab, ProjectTab, RoofsTab, StringsTab

APP_NAME = "Solar PV Designer"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1400, 860)
        self.library = load_library()
        self.rules = load_rules()
        self.project = Project()
        self.path: Path | None = None
        self.dirty = False
        self.result = None

        self.project_tab = ProjectTab()
        self.roofs_tab = RoofsTab(lambda: self.rules, lambda: self.library)
        self.equipment_tab = EquipmentTab(lambda: self.library)
        self.strings_tab = StringsTab()
        self.bom_tab = BomTab()
        self.checks_tab = ChecksTab()
        self.library_tab = LibraryTab(self.library)
        self.rules_tab = RulesTab(self.rules)

        self.tabs = QTabWidget()
        for widget, title in [(self.project_tab, "1. Project"), (self.roofs_tab, "2. Roofs"),
                              (self.equipment_tab, "3. Panels && Inverters"), (self.strings_tab, "4. Strings"),
                              (self.bom_tab, "5. BOM"), (self.checks_tab, "6. Checks"),
                              (self.library_tab, "Library"), (self.rules_tab, "BOM Rules")]:
            self.tabs.addTab(widget, title)
        self.setCentralWidget(self.tabs)
        self.status = QLabel()
        self.statusBar().addWidget(self.status, 1)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.recalculate)
        for tab in (self.project_tab, self.roofs_tab, self.equipment_tab, self.bom_tab):
            tab.changed.connect(self.on_change)
        self.library_tab.changed.connect(self.on_library_change)
        self.rules_tab.changed.connect(self.on_rules_change)
        self.bom_tab.export_requested.connect(self.export)

        self._menus()
        self.load_project(Project(), None)

    def _menus(self) -> None:
        menu = self.menuBar().addMenu("&File")
        for text, key, slot in [("&New project", QKeySequence.New, self.new),
                                ("&Open…", QKeySequence.Open, self.open),
                                ("&Save", QKeySequence.Save, self.save),
                                ("Save &as…", QKeySequence.SaveAs, self.save_as),
                                ("&Export Excel report…", "Ctrl+E", self.export)]:
            action = QAction(text, self)
            action.setShortcut(key)
            action.triggered.connect(slot)
            menu.addAction(action)
            self.addAction(action)
        menu.addSeparator()
        quit_action = QAction("E&xit", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)
        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("&About", self)
        about.triggered.connect(self.about)
        help_menu.addAction(about)

    # Project lifecycle -------------------------------------------------
    def load_project(self, project: Project, path: Path | None) -> None:
        self.project, self.path = project, path
        self.project_tab.load(project)
        self.roofs_tab.load(project)
        self.equipment_tab.load(project)
        self.dirty = False
        self.recalculate()

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, APP_NAME, "Save changes to this project first?",
                                      QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if answer == QMessageBox.Save:
            return self.save()
        return answer == QMessageBox.Discard

    def new(self) -> None:
        if self._confirm_discard():
            self.load_project(Project(), None)

    def open(self) -> None:
        if not self._confirm_discard():
            return
        start = QSettings().value("last_dir", "")
        path, _ = QFileDialog.getOpenFileName(self, "Open project", start, f"Solar PV projects (*{PROJECT_SUFFIX})")
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path) -> None:
        try:
            project = Project.load(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"Cannot open {path}:\n{exc}")
            return
        QSettings().setValue("last_dir", str(path.parent))
        self.load_project(project, path)

    def save(self) -> bool:
        if self.path is None:
            return self.save_as()
        self.project.save(self.path)
        self.dirty = False
        self._title()
        return True

    def save_as(self) -> bool:
        start = str(Path(QSettings().value("last_dir", "")) / f"{self.project.name}{PROJECT_SUFFIX}")
        path, _ = QFileDialog.getSaveFileName(self, "Save project", start, f"Solar PV projects (*{PROJECT_SUFFIX})")
        if not path:
            return False
        self.path = Path(path).with_suffix(PROJECT_SUFFIX)
        QSettings().setValue("last_dir", str(self.path.parent))
        return self.save()

    def export(self) -> None:
        if self.result is None:
            return
        start = str(Path(QSettings().value("last_dir", "")) / f"{self.project.name} - BOM.xlsx")
        path, _ = QFileDialog.getSaveFileName(self, "Export Excel report", start, "Excel (*.xlsx)")
        if not path:
            return
        try:
            export_report(self.project, self.result, path)
        except PermissionError:
            QMessageBox.warning(self, APP_NAME, "Cannot write the file. Is it open in Excel?")
            return
        QMessageBox.information(self, APP_NAME, f"Saved {path}")

    def closeEvent(self, event) -> None:
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()

    def about(self) -> None:
        QMessageBox.about(self, APP_NAME,
                          f"<b>{APP_NAME} {__version__}</b><br>Rooftop solar BOM and string design for engineers.<br>"
                          "Open source under the MIT licence.<br><br>Checks follow the PUCSL Guidelines on Rooftop Solar "
                          "PV Installation (Rev 1, 2022). Always review a design before construction.")

    # Recalculation -------------------------------------------------------
    def on_change(self) -> None:
        self.dirty = True
        self._title()
        self.timer.start()

    def on_library_change(self) -> None:
        self.equipment_tab.load(self.project)
        self.timer.start()

    def on_rules_change(self) -> None:
        self.roofs_tab.refresh_roof_types()
        self.timer.start()

    def recalculate(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.result = compute(self.project, self.library, self.rules)
        except Exception:  # noqa: BLE001 - keep the app alive and show what failed
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, APP_NAME, "Calculation failed:\n\n" + traceback.format_exc(limit=3))
            return
        QApplication.restoreOverrideCursor()
        r = self.result
        self.strings_tab.show_result(r)
        self.bom_tab.show_result(self.project, r)
        self.checks_tab.show_result(r)
        fails = sum(1 for c in r.checks if c.status == "fail")
        warns = sum(1 for c in r.checks if c.status == "warn")
        if r.design:
            self.status.setText(f"{r.design.panels_used} panels, {r.design.dc_kwp:.2f} kWp, "
                                f"{len(r.design.inverters)} inverters, {r.design.total_strings} strings. "
                                f"Checks: {fails} failed, {warns} warnings.")
        else:
            self.status.setText(r.design_error)
        self._title()

    def _title(self) -> None:
        name = self.path.name if self.path else "Untitled"
        self.setWindowTitle(f"{'*' if self.dirty else ''}{name} – {APP_NAME}")


def main() -> None:
    QApplication.setOrganizationName("SolarPVDesigner")
    QApplication.setApplicationName(APP_NAME)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    icon = Path(__file__).resolve().parent.parent / "data" / "icon.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1 and sys.argv[1].endswith(PROJECT_SUFFIX):
        window.open_path(Path(sys.argv[1]))
    sys.exit(app.exec())
