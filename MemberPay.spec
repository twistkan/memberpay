# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all


webview_datas, webview_binaries, webview_hiddenimports = collect_all("webview")
datas = webview_datas + [("templates", "templates"), ("static", "static"), ("app_icon.ico", "."), ("licenses", "licenses")]

a = Analysis(
    ["desktop_host.py"],
    pathex=[".", ".vendor"],
    binaries=webview_binaries,
    datas=datas,
    hiddenimports=webview_hiddenimports,
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
    name="MemberPay",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="app_icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    a.zipfiles,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MemberPay",
)
