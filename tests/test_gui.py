"""Start the real window offscreen and run a design through it."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_window_runs_a_design(app):
    from solarpv.gui.app import MainWindow
    from solarpv.project import InverterChoice, Project, Roof

    window = MainWindow()
    project = Project(name="GUI", panel_key="JA Solar JAM72D40-595/LB",
                      inverters=[InverterChoice("SolaX X3-FTH-75K", 1)],
                      roofs=[Roof("Roof", [[1] * 12 for _ in range(14)])])
    window.load_project(project, None)
    assert window.result.design is not None, window.result.design_error
    assert window.bom_tab.table.rowCount() > 20
    assert window.strings_tab.inverters.rowCount() == 1
    for i in range(window.tabs.count()):
        window.tabs.setCurrentIndex(i)
        app.processEvents()
    window.dirty = False
    window.close()
