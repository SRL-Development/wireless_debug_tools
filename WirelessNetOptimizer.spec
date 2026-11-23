# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

ROOT = Path(SPECPATH)
APP_DIR = ROOT / 'wireless_app'

a = Analysis(
    [str(APP_DIR / 'app.py')],
    pathex=[str(APP_DIR)],
    binaries=[],
    datas=[(str(APP_DIR / 'web'), 'wireless_app/web'),
           (str(APP_DIR / 'data'), 'wireless_app/data')],
    hiddenimports=['wlan_scan', 'air_quality', 'roam_monitor', 'survey', 'extras', 'desktop'],
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
    a.binaries,
    a.datas,
    [],
    name='WirelessNetOptimizer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
