# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Morgana Companion (onedir, windowed, Windows & Linux).

Build with:
    pyinstaller --noconfirm --clean MorganaCompanion.spec
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH)
ICON = str(ROOT / "Morgana" / "morgana.ico") if sys.platform.startswith("win") else None

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "Morgana"), "Morgana"),
        (str(ROOT / "fonts"), "fonts"),
        (str(ROOT / "morgana_list.md"), "."),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Morgana Companion",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon=ICON,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Morgana Companion",
)
