# -*- mode: python ; coding: utf-8 -*-
# PyInstaller Spec ファイル: x_auto_dm.spec (他PC配布用・Playwright同梱対応)
import os
import sys

block_cipher = None

pw_datas = []
pw_binaries = []
pw_hidden = []
try:
    from PyInstaller.utils.hooks import collect_all
    pw_datas, pw_binaries, pw_hidden = collect_all('playwright')
except Exception:
    pass

xl_datas = []
xl_binaries = []
xl_hidden = []
try:
    from PyInstaller.utils.hooks import collect_all
    xl_datas, xl_binaries, xl_hidden = collect_all('openpyxl')
except Exception:
    pass

a = Analysis(
    ['gui_launcher.pyw'],
    pathex=[],
    binaries=pw_binaries + xl_binaries,
    datas=[
        ('config_manager.py', '.'),
        ('browser_controller.py', '.'),
        ('db_manager.py', '.'),
        ('main.py', '.'),
        ('test_single.py', '.'),
        ('edit_account.py', '.'),
        ('profile_launcher.py', '.'),
        ('profile_luncher.py', '.'),
    ] + pw_datas + xl_datas,
    hiddenimports=[
        'main',
        'test_single',
        'edit_account',
        'profile_launcher',
        'profile_luncher',
        'browser_controller',
        'config_manager',
        'db_manager',
        'playwright',
        'playwright.sync_api',
        'openpyxl',
        'sqlite3',
        'tkinter',
    ] + pw_hidden + xl_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'numpy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='x_auto_dm_gui',
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
    icon=None
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='x_auto_dm_tool',
)
