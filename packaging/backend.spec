# PyInstaller spec for the frozen pandda-inspect backend (ROADMAP #6 spike).
#
# Django resolves a lot by string at runtime (settings, apps, migrations,
# management commands, DRF/spectacular internals), so we collect whole packages
# rather than rely on import-graph analysis. gemmi is a compiled extension —
# collect_dynamic_libs pulls its shared lib. Build from the repo root:
#   pyinstaller packaging/backend.spec --noconfirm
import os
from PyInstaller.utils.hooks import (
    collect_submodules,
    collect_data_files,
    collect_dynamic_libs,
)

ROOT = os.path.abspath(os.getcwd())

hidden = ["dotenv", "gemmi"]
for pkg in (
    "config",
    "inspect_api",
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
    "waitress",
    "dotenv",
):
    hidden += collect_submodules(pkg)
# Django pulls db backends / management commands dynamically.
hidden += collect_submodules("django.db.backends.sqlite3")
hidden += collect_submodules("django.core.management.commands")

datas = []
datas += collect_data_files("drf_spectacular")  # swagger/schema templates
datas += collect_data_files("inspect_api")      # migrations etc.

binaries = collect_dynamic_libs("gemmi")

# Ship the project source so config/ and inspect_api/ import inside the bundle.
datas += [
    (os.path.join(ROOT, "config"), "config"),
    (os.path.join(ROOT, "inspect_api"), "inspect_api"),
]

a = Analysis(
    [os.path.join(ROOT, "packaging", "server_main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pandda-inspect-backend",
    console=True,
    disable_windowed_traceback=False,
    strip=False,
    upx=False,
)
