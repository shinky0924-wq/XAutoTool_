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

# 存在するPythonスクリプトのみ安全に追加 (ファイル未存在時のPyInstallerエラーを完全防止)
extra_py_files = [
    'config_manager.py',
    'browser_controller.py',
    'db_manager.py',
    'main.py',
    'test_single.py',
    'edit_account.py',
    'profile_launcher.py',
    'profile_luncher.py',
]
extra_datas = [(f, '.') for f in extra_py_files if os.path.isfile(f)]

a = Analysis(
    ['gui_launcher.pyw'],
    pathex=[],
    binaries=pw_binaries + xl_binaries,
    datas=extra_datas + pw_datas + xl_datas,
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

# プロファイルランチャーの単体EXE化 (存在する場合のみ安全に追加)
prof_entry = None
if os.path.isfile('profile_launcher.py'):
    prof_entry = 'profile_launcher.py'
elif os.path.isfile('profile_luncher.py'):
    prof_entry = 'profile_luncher.py'

if prof_entry:
    prof_support_files = ['browser_controller.py', 'config_manager.py', 'db_manager.py']
    prof_datas = [(f, '.') for f in prof_support_files if os.path.isfile(f)] + pw_datas + xl_datas
    a_prof = Analysis(
        [prof_entry],
        pathex=[],
        binaries=pw_binaries + xl_binaries,
        datas=prof_datas,
        hiddenimports=[
            'profile_launcher',
            'profile_luncher',
            'browser_controller',
            'config_manager',
            'db_manager',
            'playwright',
            'playwright.sync_api',
            'openpyxl',
            'sqlite3',
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
    pyz_prof = PYZ(a_prof.pure, a_prof.zipped_data, cipher=block_cipher)
    exe_prof = EXE(
        pyz_prof,
        a_prof.scripts,
        [],
        exclude_binaries=True,
        name='profile_launcher',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=None
    )
    coll_exes = [exe, exe_prof]
    coll_binaries = a.binaries + a_prof.binaries
    coll_zipfiles = a.zipfiles + a_prof.zipfiles
    coll_datas = a.datas + a_prof.datas
else:
    coll_exes = [exe]
    coll_binaries = a.binaries
    coll_zipfiles = a.zipfiles
    coll_datas = a.datas

coll = COLLECT(
    *coll_exes,
    coll_binaries,
    coll_zipfiles,
    coll_datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='x_auto_dm_tool',
)
