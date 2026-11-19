# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['wireless_app/app.py'],
    pathex=[],
    binaries=[],
    datas=[('C:/Users/Administrator/WorkBuddy/2026-11-08-18-56-25/wireless_app/web', 'wireless_app/web'), ('C:/Users/Administrator/WorkBuddy/2026-11-08-18-56-25/wireless_app/data', 'wireless_app/data')],
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
