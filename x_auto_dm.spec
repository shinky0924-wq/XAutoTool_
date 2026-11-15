# -*- mode: python ; coding: utf-8 -*-
# PyInstaller Spec ファイル: x_auto_dm.spec (単一EXE + 必要最小限構成)
import os

block_cipher = None
SPEC_DIR = SPECPATH if "SPECPATH" in globals() else os.path.abspath(".")


def safe_collect_all(pkg_name):
    try:
        from PyInstaller.utils.hooks import collect_all
        return collect_all(pkg_name)
    except Exception:
        return [], [], []


pw_datas, pw_binaries, pw_hidden = safe_collect_all('playwright')
xl_datas, xl_binaries, xl_hidden = safe_collect_all('openpyxl')
et_datas, et_binaries, et_hidden = safe_collect_all('et_xmlfile')
gr_datas, gr_binaries, gr_hidden = safe_collect_all('greenlet')
pyee_datas, pyee_binaries, pyee_hidden = safe_collect_all('pyee')

# ※ 注意: .pyソースファイルをdatasに複製同梱しない（全実行モジュールはhiddenimports経由でPYZにコンパイル格納）
a = Analysis(
    [os.path.join(SPEC_DIR, 'gui_launcher.py')],
    pathex=[SPEC_DIR],
    binaries=pw_binaries + xl_binaries + et_binaries + gr_binaries + pyee_binaries,
    datas=pw_datas + xl_datas + et_datas + gr_datas + pyee_datas,
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
        'greenlet',
        'pyee',
        'pyee.asyncio',
        'typing_extensions',
        'openpyxl',
        'et_xmlfile',
        'sqlite3',
        'tkinter',
        '_tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
        'tkinter.scrolledtext',
        'tkinter.filedialog',
        'tkinter.simpledialog',
        'tkinter.font',
        'concurrent.futures',
        'urllib.request',
        'urllib.parse',
        'urllib.error',
        'ctypes',
        'ctypes.wintypes',
        'multiprocessing',
        'json',
        'ssl',
        'winreg',
        'time',
        'shutil',
        'tempfile',
        'traceback',
        'datetime',
        'functools',
        'threading',
        'subprocess',
        'hashlib',
        'html',
    ] + pw_hidden + xl_hidden + et_hidden + gr_hidden + pyee_hidden,
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

# UPX無効化: WindowsでのDLL破損・Playwrightドライバ異常・セキュリティ誤検知によるEXE起動不能を防止
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='x_auto_dm_gui',
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
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='x_auto_dm_tool',
)
