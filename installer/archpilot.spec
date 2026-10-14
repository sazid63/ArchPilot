# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build definition for ArchPilot.

Produces a single windowed ``ArchPilot.exe``.

FreeCAD is deliberately **not** bundled. It is an independent installation that ArchPilot drives
as a separate process, so it must exist on the target machine — the installer prompts for it, or
the user points at it in Settings. Bundling FreeCAD would mean redistributing a large third-party
application and would defeat the point of driving it as an external program.

Build from the repository root:

    pyinstaller installer/archpilot.spec --noconfirm

The result is ``dist/ArchPilot/ArchPilot.exe``.
"""
from pathlib import Path

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

#: The repository root (one level up from this spec file).
ROOT = Path(SPECPATH).resolve().parent

# ReportLab reads font metrics and other assets from its package directory at runtime, so they
# must travel with the executable.
datas = collect_data_files("reportlab")
datas += [
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "LICENSE"), "."),
]

# Pydantic v2 resolves some validator modules dynamically; collect them explicitly so a frozen
# build validates designs exactly as the source does.
hiddenimports = collect_submodules("pydantic")

# Packages that reach the bundle only through a dependency's *optional* import, and that ArchPilot
# never touches at runtime. Each is guarded by ``try``/``except ImportError`` or an
# ``if TYPE_CHECKING`` block in whichever library mentions it, so the library falls back cleanly:
#
#   openpyxl  -> numpy, lxml            (used only when present; the standard library otherwise)
#   pydantic  -> rich                   (imported only under ``if TYPE_CHECKING``)
#   pydantic  -> zoneinfo/tzdata        (only for a timezone field; ArchPilot defines none)
#   reportlab -> charset_normalizer     (only for sniffing an XML prologue)
#   numpy     -> psutil, PyYAML
#   rich      -> pygments, markdown-it, attrs
#   lxml      -> beautifulsoup4, soupsieve
#   openpyxl  -> defusedxml             (only when *reading* XML; ArchPilot only writes workbooks)
#   pydantic.v1 -> dotenv               (only for the v1 BaseSettings shim; see below)
#   Pillow    -> defusedxml             (only for ``Image.getxmp``)
#
# Pillow is *not* in this list: although openpyxl also uses it only when present, ReportLab imports
# it unconditionally, so it must stay. Its unused codecs are dropped from the binary payload below.
#
# ``pydantic.mypy`` and ``pydantic.v1.mypy`` are mypy plugins: ``collect_submodules("pydantic")``
# above sweeps them in, but only mypy ever imports them, and they drag in ``tomli``.
#
# This list is not a guess. The whole test suite passes with every one of these packages hard
# blocked at import time, which is precisely the condition the frozen application runs under.
unused_optional_packages = [
    "numpy",
    "lxml",
    "rich",
    "pygments",
    "bs4",
    "soupsieve",
    "attr",
    "attrs",
    "markdown_it",
    "charset_normalizer",
    "yaml",
    "psutil",
    "tomli",
    "tzdata",
    "pydantic.mypy",
    "pydantic.v1.mypy",
    # ``collect_submodules("pydantic")`` above sweeps in the whole v1 compatibility shim. Pydantic v2
    # reaches it from exactly one place -- a function-scoped ``from pydantic.v1 import BaseModel`` in
    # ``_generate_schema`` that only runs for a type carrying ``__get_validators__``, a v1 construct.
    # ArchPilot's schemas are pure v2, so the shim is never imported; dropping it is the largest single
    # saving left, and it is what pulled ``dotenv`` in as well.
    "pydantic.v1",
    "dotenv",
    "defusedxml",
]

# Qt ships far more than a widgets application uses. Excluding the heavy, clearly-unused modules
# keeps the bundle to a reasonable size without touching anything the interface relies on.
excludes = [
    "tkinter",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.QtTest",
    "PySide6.QtSql",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech",
] + unused_optional_packages

a = Analysis(
    [str(ROOT / "archpilot" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# The PySide6 hook collects a fixed set of Qt shared libraries and plugins regardless of which Qt
# modules are imported. These are unreachable from the modules ArchPilot actually uses (QtCore,
# QtGui, QtWidgets, QtNetwork): verified by walking the PE import tables of the kept DLLs, with no
# delay-load edges reaching them. Qt6Quick/Qt6Qml/Qt6Pdf/Qt6Svg/Qt6OpenGL are the QML, PDF and SVG
# runtimes, none of which a QWidgets application loads; ``opengl32sw.dll`` is Mesa's software
# OpenGL fallback (~20 MB) and is only consulted when a GL context is created. The plugins that
# link against Qt6Svg/Qt6Pdf are removed with them, so nothing points at a missing library.
unused_qt_libraries = {
    "opengl32sw",  # Mesa software-OpenGL fallback (~20 MB); a widgets app never makes a GL context
    "qt6opengl",
    "qt6quick",
    "qt6qml",
    "qt6qmlmeta",
    "qt6qmlmodels",
    "qt6qmlworkerscript",
    "qt6virtualkeyboard",
    "qt6pdf",
    "qt6svg",
}
unused_qt_plugins = {
    "qsvgicon",  # icon engine for SVG icons (ArchPilot ships none)
    "qsvg",  # SVG image format
    "qpdf",  # PDF image format
    "qtvirtualkeyboardplugin",
}
# ReportLab hard-imports Pillow, so it has to travel with the build, but ArchPilot's exports contain
# no images and none of Pillow's codecs are ever exercised. Pillow imports each codec lazily and
# swallows a missing one (``Image.init`` wraps every plugin import in ``try``/``except ImportError``),
# so dropping them only means those formats cannot be opened -- which this application never does.
unused_pillow_codecs = {
    "_avif",  # AV1/AVIF codec (~7.5 MB)
    "_webp",
    "_imagingcms",
    "_imagingmath",
    "_imagingtk",
}
# Match on the first dot-segment so versioned names (``_avif.cp314-win_amd64.pyd``) are caught without
# pinning a Python version or ABI tag into this file.
dropped_stems = unused_qt_libraries | unused_qt_plugins | unused_pillow_codecs

a.binaries = TOC([
    entry
    for entry in a.binaries
    if Path(entry[0].replace("\\", "/")).name.lower().split(".")[0] not in dropped_stems
])

# The only translation catalogues Qt ships are for languages other than the built-in English
# strings, so an English-only interface never loads them.
a.datas = TOC([
    entry
    for entry in a.datas
    if "pyside6/translations/" not in entry[0].replace("\\", "/").lower()
])

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ArchPilot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ArchPilot",
)
