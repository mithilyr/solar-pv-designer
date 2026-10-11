# PyInstaller spec for the Windows build. Run from the repository root:
#   pyinstaller packaging/solarpv.spec --noconfirm
from pathlib import Path

root = Path(SPECPATH).parent
data = root / "src" / "solarpv" / "data"

a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root / "src")],
    datas=[(str(p), "solarpv/data") for p in data.iterdir() if p.suffix in {".json", ".ico", ".png"}],
    hiddenimports=["solarpv.data"],
    excludes=["tkinter", "matplotlib", "numpy", "PySide6.QtWebEngineCore", "PySide6.QtQuick", "PySide6.QtQml",
              "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SolarPVDesigner",
    icon=str(data / "icon.ico"),
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="SolarPVDesigner")
