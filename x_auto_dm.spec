# -*- mode: python ; coding: utf-8 -*-
# PyInstaller Spec ファイル: x_auto_dm.spec (単一EXE + 必要最小限構成)
import os

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

# ※ 注意: .pyソースファイルをdatasに複製同梱しない（全実行モジュールはhiddenimports経由でPYZにコンパイル格納）
a = Analysis(
    ['gui_launcher.py'],
    pathex=[],
    binaries=pw_binaries + xl_binaries,
    datas=pw_datas + xl_datas,
    hiddenimports=[
        'main',
        'ai_generator',
        'test_single',
        'edit_account',
        'chrome_launcher',
        'browser_controller',
        'config_manager',
        'db_manager',
        'playwright',
        'playwright.sync_api',
        'openpyxl',
        'sqlite3',
        'tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
        'tkinter.scrolledtext',
        'tkinter.filedialog',
        'tkinter.simpledialog',
        'concurrent.futures',
        'urllib.request',
        'urllib.parse',
        'urllib.error',
        'json',
        'ssl',
        'winreg',
    ] + pw_hidden + xl_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'build_dist_package',
        'diagnose',
        'profile_launcher',
        'profile_luncher',
        'run_launcher',
        'PyInstaller',
        'pip',
        'setuptools',
        'wheel',
        'unittest',
        'pydoc',
        'doctest',
        'matplotlib',
        'scipy',
        'numpy',
        'pandas',
        'PIL',
    ],
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
    icon=None,
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
