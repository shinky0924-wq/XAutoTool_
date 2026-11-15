"""
他PC配布用パッケージ自動作成スクリプト (build_dist_package.py)
このPCでPythonコードを単一EXE (x_auto_dm_gui.exe) へコンパイルし、
Playwright Chromiumブラウザを同梱して、他PCではZIP解凍だけで即起動できる最小構成パッケージを作成します。
さらに Windows 11 スマート アプリ コントロール (Device Guard) で未署名EXEがブロックされる環境向けに、
署名済みPython経由で即動作する _app/ ランタイムと自動フォールバック起動バッチも同梱します。
"""

import os
import sys
import stat
import time
import shutil
import zipfile
import datetime
import py_compile
import subprocess

# Windows CP932 / Emoji UnicodeEncodeError 対策 (コンソール直接実行時)
if sys.platform == "win32":
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

base_dir = os.path.dirname(os.path.abspath(__file__))


def _on_rm_error(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def force_rmtree(target_path: str, label: str = ""):
    """古いビルド成果物を確実に削除する。ロック等で削除できない場合は明示エラーで停止する。"""
    if not os.path.exists(target_path):
        return
    for _ in range(3):
        try:
            shutil.rmtree(target_path, onerror=_on_rm_error)
        except Exception:
            pass
        if not os.path.exists(target_path):
            return
        time.sleep(0.5)
    raise RuntimeError(
        f"古いフォルダを削除できませんでした ({label or target_path})。\n"
        f"x_auto_dm_gui.exe やエクスプローラーでフォルダが開かれていないか確認して閉じてください。"
    )


def kill_running_dist_exe():
    """配布フォルダ内の x_auto_dm_gui.exe が起動中なら強制終了してロックを解除する。"""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "x_auto_dm_gui.exe"],
                capture_output=True,
                timeout=5,
            )
            time.sleep(0.4)
        except Exception:
            pass


def clear_pycache(root_dir: str):
    """古い .pyc キャッシュによる旧コード混入を防ぐため __pycache__ を削除。"""
    for root, dirs, _ in os.walk(root_dir):
        if "build" in root.split(os.sep) or "dist" in root.split(os.sep):
            continue
        for d in list(dirs):
            if d == "__pycache__":
                p = os.path.join(root, d)
                shutil.rmtree(p, ignore_errors=True)
                dirs.remove(d)


def is_smart_app_control_enforced() -> bool:
    """Windows 11 Smart App Control (VerifiedAndReputablePolicyState == 1) を検出"""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy") as k:
            val, _ = winreg.QueryValueEx(k, "VerifiedAndReputablePolicyState")
            return int(val) == 1
    except Exception:
        return False


print("=" * 70)
print("  📦 他PC配布用パッケージ自動作成 (最新版EXE + Smart App Control回避対応)")
print("  ★ 通常PCはEXEで即起動 / Device Guard有効PCは自動Python起動へフォールバック")
print(f"  🕒 ビルド開始時刻: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"  🐍 使用Python: {sys.executable} (v{sys.version.split()[0]})")
print("=" * 70)

# 0. 事前チェック: ローカルPythonコードの構文チェック & キャッシュ掃除
core_py_files = [
    "gui_launcher.py",
    "main.py",
    "browser_controller.py",
    "config_manager.py",
    "db_manager.py",
    "ai_generator.py",
    "test_single.py",
    "edit_account.py",
    "chrome_launcher.py",
]
print("\n[0/5] 最新ソースコードの確認・キャッシュクリア...")
clear_pycache(base_dir)
for py_name in core_py_files:
    py_path = os.path.join(base_dir, py_name)
    if not os.path.isfile(py_path):
        print(f"❌ 必須ソースファイルが見つかりません: {py_name}")
        sys.exit(1)
    try:
        py_compile.compile(py_path, doraise=True)
        mtime_str = datetime.datetime.fromtimestamp(os.path.getmtime(py_path)).strftime("%Y-%m-%d %H:%M:%S")
        print(f"   ✓ {py_name} (更新: {mtime_str})")
    except py_compile.PyCompileError as ce:
        print(f"❌ 構文エラーがあります ({py_name}): {ce}")
        sys.exit(1)

# 1. 依存ライブラリの確認 & インストール
print("\n[1/5] PyInstaller および必要パッケージの確認...")
required_mods = [
    ("PyInstaller", "pyinstaller"),
    ("openpyxl", "openpyxl"),
    ("et_xmlfile", "et_xmlfile"),
    ("playwright", "playwright"),
    ("greenlet", "greenlet"),
    ("pyee", "pyee"),
    ("tkinter", None),
    ("sqlite3", None),
]

missing_pkgs = []
for mod_name, pip_name in required_mods:
    try:
        __import__(mod_name)
    except Exception:
        if pip_name:
            missing_pkgs.append((mod_name, pip_name))
        else:
            print(f"❌ Python標準ライブラリ '{mod_name}' が利用できません。")
            sys.exit(1)

if missing_pkgs:
    pip_targets = sorted(set(p for _, p in missing_pkgs))
    print(f"   ・不足パッケージをインストール中: {', '.join(pip_targets)}")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", *pip_targets],
            check=True,
        )
    except Exception as e:
        print(f"❌ pip インストールに失敗しました: {e}")
        sys.exit(1)

for mod_name, _ in required_mods:
    try:
        __import__(mod_name)
    except Exception as e:
        print(f"❌ 必須モジュール '{mod_name}' を読み込めません: {e}")
        sys.exit(1)

import PyInstaller
print(f"   ✅ 全必須パッケージ確認完了 (PyInstaller {getattr(PyInstaller, '__version__', 'OK')})")

# 2. Playwright Chromium ブラウザの確認・ダウンロード
print("\n[2/5] Playwright Chromium ブラウザの確認・準備...")
try:
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    print("   ✅ Playwright Chromium ブラウザの準備完了")
except Exception as e:
    print(f"⚠️ Playwright ブラウザ準備警告 (オフライン時は既存キャッシュを使用): {e}")

# 3. PyInstaller による単一EXE化コンパイル (実行中EXE停止 + 古い成果物を確実削除)
print("\n[3/5] 最新ソースコードをEXE化コンパイル中 (PyInstaller)...")
kill_running_dist_exe()

dist_root = os.path.join(base_dir, "dist")
dist_src = os.path.join(dist_root, "x_auto_dm_tool")
build_tmp = os.path.join(base_dir, "build")
out_dir = os.path.join(dist_root, "【他PC用】x_auto_dm_tool")

try:
    force_rmtree(dist_src, "dist/x_auto_dm_tool")
    force_rmtree(build_tmp, "build")
    force_rmtree(out_dir, "dist/【他PC用】x_auto_dm_tool")
except RuntimeError as lock_err:
    print(f"❌ {lock_err}")
    sys.exit(1)

spec_path = os.path.join(base_dir, "x_auto_dm.spec")
if not os.path.isfile(spec_path):
    print(f"❌ x_auto_dm.spec が見つかりません: {spec_path}")
    sys.exit(1)

compile_start = time.time()
res = subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        spec_path,
        "--noconfirm",
        "--clean",
        "--distpath",
        dist_root,
        "--workpath",
        build_tmp,
    ],
    cwd=base_dir,
)
if res.returncode != 0:
    print("❌ PyInstaller コンパイルに失敗しました。")
    sys.exit(1)

built_exe = os.path.join(dist_src, "x_auto_dm_gui.exe")
built_internal = os.path.join(dist_src, "_internal")
if not os.path.isfile(built_exe) or not os.path.isdir(built_internal):
    print("❌ EXEコンパイル後の出力ファイル (x_auto_dm_gui.exe / _internal) が見つかりません。")
    sys.exit(1)

exe_mtime = os.path.getmtime(built_exe)
if exe_mtime < compile_start - 5.0:
    print("❌ x_auto_dm_gui.exe のタイムスタンプが更新されていません（古いキャッシュの可能性）。")
    sys.exit(1)

print(f"   ✅ 最新版EXEコンパイル完了 (x_auto_dm_gui.exe / サイズ: {os.path.getsize(built_exe):,} bytes)")

# (A-0) オプション: 署名証明書 (codesign.pfx / SIGN_PFX_PATH) があれば EXE を自動署名 & Zone.Identifier 解除
def try_sign_and_unblock(exe_path: str, dist_folder: str):
    if sys.platform != "win32":
        return
    pfx_path = os.environ.get("SIGN_PFX_PATH", "").strip().strip('"')
    if not pfx_path:
        cand_pfx = os.path.join(base_dir, "codesign.pfx")
        if os.path.isfile(cand_pfx):
            pfx_path = cand_pfx
    if pfx_path and os.path.isfile(pfx_path):
        signtool_cands = [shutil.which("signtool") or ""]
        pf86 = os.environ.get("ProgramFiles(x86)", "")
        if pf86:
            kits_bin = os.path.join(pf86, "Windows Kits", "10", "bin")
            if os.path.isdir(kits_bin):
                for root, _, files in os.walk(kits_bin):
                    if "signtool.exe" in files and ("x64" in root.lower() or "x86" in root.lower()):
                        signtool_cands.append(os.path.join(root, "signtool.exe"))
        signtool_exe = next((s for s in signtool_cands if s and os.path.isfile(s)), "")
        if signtool_exe:
            pfx_pw = os.environ.get("SIGN_PFX_PASS", "")
            cmd = [signtool_exe, "sign", "/fd", "SHA256", "/f", pfx_path]
            if pfx_pw:
                cmd.extend(["/p", pfx_pw])
            cmd.extend(["/tr", "http://timestamp.digicert.com", "/td", "SHA256", exe_path])
            try:
                sres = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if sres.returncode == 0:
                    print("   ✅ コード署名証明書で x_auto_dm_gui.exe に署名しました")
                else:
                    print(f"   ⚠️ EXE署名スキップ: {(sres.stderr or sres.stdout or '').strip()[:120]}")
            except Exception as se:
                print(f"   ⚠️ EXE署名スキップ: {se}")

# 4. 超シンプル・重複なしの他PC配布用フォルダを作成
print("\n[4/5] 他PC配布用フォルダを整理中...")
os.makedirs(out_dir, exist_ok=True)
try_sign_and_unblock(built_exe, out_dir)

# (A) PyInstaller出力物 (x_auto_dm_gui.exe および _internal) をコピー
for item in os.listdir(dist_src):
    s = os.path.join(dist_src, item)
    d = os.path.join(out_dir, item)
    if os.path.isdir(s):
        shutil.copytree(s, d, dirs_exist_ok=True)
    else:
        shutil.copy2(s, d)

# (A-2) Smart App Control / Device Guard 回避用 _app ランタイムスクリプトを同梱
app_sub_dir = os.path.join(out_dir, "_app")
os.makedirs(app_sub_dir, exist_ok=True)
for py_name in core_py_files:
    shutil.copy2(os.path.join(base_dir, py_name), os.path.join(app_sub_dir, py_name))
req_src = os.path.join(base_dir, "requirements.txt")
if os.path.isfile(req_src):
    shutil.copy2(req_src, os.path.join(app_sub_dir, "requirements.txt"))
else:
    with open(os.path.join(app_sub_dir, "requirements.txt"), "w", encoding="utf-8") as rf:
        rf.write("openpyxl\nplaywright\n")
print("   ✅ Device Guard / Smart App Control 回避用 _app ランタイムを同梱完了")

# (B) Playwright Chromiumブラウザを同梱 (_internal/ms-playwright 内に完全格納)
print("   ・Playwright Chromiumブラウザ本体を同梱中 (他PCダウンロード不要)...")
local_appdata = os.environ.get("LOCALAPPDATA", "")
user_profile = os.environ.get("USERPROFILE", "")
pw_candidates = [
    os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
    os.path.join(local_appdata, "ms-playwright") if local_appdata else "",
    os.path.join(user_profile, "AppData", "Local", "ms-playwright") if user_profile else "",
]
found_pw_src = ""
for p in pw_candidates:
    if p and p != "0" and os.path.isdir(p):
        if any(name.startswith("chromium") for name in os.listdir(p)):
            found_pw_src = p
            break

bundled_browsers = []
if found_pw_src:
    try:
        target_pw = os.path.join(out_dir, "_internal", "ms-playwright")
        os.makedirs(target_pw, exist_ok=True)
        for item in os.listdir(found_pw_src):
            s = os.path.join(found_pw_src, item)
            d = os.path.join(target_pw, item)
            if os.path.isdir(s) and item.startswith(("chromium-", "chromium_headless_shell-", "ffmpeg-", "winldd-")):
                shutil.copytree(s, d, dirs_exist_ok=True)
                bundled_browsers.append(item)
        if bundled_browsers:
            print(f"   ✅ Playwright Chromiumブラウザ同梱完了 ({', '.join(bundled_browsers)})")
        else:
            print("⚠️ Chromium本体ディレクトリが ms-playwright 内に見つかりませんでした。")
    except Exception as e:
        print(f"⚠️ ブラウザ同梱警告: {e}")
else:
    print("⚠️ ms-playwright フォルダが見つかりませんでした (他PCにChrome/Edgeがある場合は動作します)。")

# (C) ユーザー設定用Excelファイルをコピー (一時ロックファイル ~$*.xlsx は除外)
copied_excels = []
for fname in sorted(os.listdir(base_dir)):
    if fname.endswith(".xlsx") and not fname.startswith("~$"):
        fpath = os.path.join(base_dir, fname)
        if os.path.isfile(fpath):
            shutil.copy2(fpath, os.path.join(out_dir, fname))
            copied_excels.append(fname)
print(f"   ✅ 設定用Excelファイルの同梱完了 ({len(copied_excels)}ファイル)")

# (C-2) logフォルダ・dbフォルダ・スクショフォルダ・browser_profilesの作成
log_dist = os.path.join(out_dir, "logフォルダ")
db_dist = os.path.join(out_dir, "dbフォルダ")
ss_dist = os.path.join(out_dir, "スクショフォルダ")
profiles_dist = os.path.join(out_dir, "browser_profiles")
os.makedirs(log_dist, exist_ok=True)
os.makedirs(db_dist, exist_ok=True)
os.makedirs(ss_dist, exist_ok=True)
os.makedirs(profiles_dist, exist_ok=True)

try:
    with open(os.path.join(log_dist, "README_ログについて.txt"), "w", encoding="utf-8") as f:
        f.write("実行されたアカウントごとに、ここに「アカウント名.log」（例: wqwq1320.log）が自動生成されます。\n")
    with open(os.path.join(db_dist, "README_データベースについて.txt"), "w", encoding="utf-8") as f:
        f.write("実行されたアカウントごとに、ここに「アカウント名.DB」（例: wqwq1320.DB / 重複防止SQLite3）が自動生成されます。\n")
    with open(os.path.join(ss_dist, "README_スクショについて.txt"), "w", encoding="utf-8") as f:
        f.write("ログイン失敗・DM/リプライ/フォロー/いいね失敗・例外発生時のブラウザ画面スクリーンショット(PNG)が自動保存されます。\n")
    with open(os.path.join(profiles_dist, "README_ブラウザプロファイルについて.txt"), "w", encoding="utf-8") as f:
        f.write("アカウントごとのログインセッション・Cookieが自動保存されます。GUIの「👤 Chromeプロファイル起動」からも作成可能です。\n")
except Exception:
    pass

# (D) 他PC用起動バッチ & CMDなし起動VBS
# ・パス内のスペースや括弧 (53) 等で cmd.exe が構文エラーにならないよう、if(...) ブロックではなく goto ベースで構築
# ・Smart App Control (VerifiedAndReputablePolicyState == 0x1) または EXE ブロック時は自動で署名済み Python (_app\gui_launcher.py) へフォールバック
# ・必ず pythonw.exe を優先解決し、黒いCMD画面とアプリの2重起動を完全に防止

PYTHON_FINDER_SUBS = (
    ":FindPython\r\n"
    'set "PY="\r\n'
    'set "PY_ARG="\r\n'
    'set "PYW="\r\n'
    'set "PYW_ARG="\r\n'
    'if exist "%BASE_DIR%.venv\\Scripts\\python.exe" call :TryPy "%BASE_DIR%.venv\\Scripts\\python.exe" ""\r\n'
    'if not defined PY if exist "%BASE_DIR%venv\\Scripts\\python.exe" call :TryPy "%BASE_DIR%venv\\Scripts\\python.exe" ""\r\n'
    'if not defined PY if exist "%LocalAppData%\\Programs\\Python\\Python313\\python.exe" call :TryPy "%LocalAppData%\\Programs\\Python\\Python313\\python.exe" ""\r\n'
    'if not defined PY if exist "%LocalAppData%\\Programs\\Python\\Python312\\python.exe" call :TryPy "%LocalAppData%\\Programs\\Python\\Python312\\python.exe" ""\r\n'
    'if not defined PY if exist "%LocalAppData%\\Programs\\Python\\Python311\\python.exe" call :TryPy "%LocalAppData%\\Programs\\Python\\Python311\\python.exe" ""\r\n'
    'if not defined PY if exist "%LocalAppData%\\Programs\\Python\\Python310\\python.exe" call :TryPy "%LocalAppData%\\Programs\\Python\\Python310\\python.exe" ""\r\n'
    'if not defined PY if exist "C:\\Python313\\python.exe" call :TryPy "C:\\Python313\\python.exe" ""\r\n'
    'if not defined PY if exist "C:\\Python312\\python.exe" call :TryPy "C:\\Python312\\python.exe" ""\r\n'
    'if not defined PY if exist "C:\\Python311\\python.exe" call :TryPy "C:\\Python311\\python.exe" ""\r\n'
    'if not defined PY if exist "%ProgramFiles%\\Python313\\python.exe" call :TryPy "%ProgramFiles%\\Python313\\python.exe" ""\r\n'
    'if not defined PY if exist "%ProgramFiles%\\Python312\\python.exe" call :TryPy "%ProgramFiles%\\Python312\\python.exe" ""\r\n'
    'if not defined PY call :TryPy "py" "-3.13"\r\n'
    'if not defined PY call :TryPy "py" "-3.12"\r\n'
    'if not defined PY call :TryPy "py" "-3.11"\r\n'
    'if not defined PY call :TryPy "py" "-3"\r\n'
    'if not defined PY call :TryPy "py" ""\r\n'
    'if not defined PY call :TryPy "python" ""\r\n'
    "if not defined PY exit /b 1\r\n"
    "call :ResolvePyw\r\n"
    "exit /b 0\r\n"
    "\r\n"
    ":ResolvePyw\r\n"
    'if exist "%BASE_DIR%.venv\\Scripts\\pythonw.exe" set "PYW=%BASE_DIR%.venv\\Scripts\\pythonw.exe" & exit /b 0\r\n'
    'if exist "%BASE_DIR%venv\\Scripts\\pythonw.exe" set "PYW=%BASE_DIR%venv\\Scripts\\pythonw.exe" & exit /b 0\r\n'
    'if exist "%LocalAppData%\\Programs\\Python\\Python313\\pythonw.exe" set "PYW=%LocalAppData%\\Programs\\Python\\Python313\\pythonw.exe" & exit /b 0\r\n'
    'if exist "%LocalAppData%\\Programs\\Python\\Python312\\pythonw.exe" set "PYW=%LocalAppData%\\Programs\\Python\\Python312\\pythonw.exe" & exit /b 0\r\n'
    'if exist "%LocalAppData%\\Programs\\Python\\Python311\\pythonw.exe" set "PYW=%LocalAppData%\\Programs\\Python\\Python311\\pythonw.exe" & exit /b 0\r\n'
    'if exist "%LocalAppData%\\Programs\\Python\\Python310\\pythonw.exe" set "PYW=%LocalAppData%\\Programs\\Python\\Python310\\pythonw.exe" & exit /b 0\r\n'
    'if exist "C:\\Python313\\pythonw.exe" set "PYW=C:\\Python313\\pythonw.exe" & exit /b 0\r\n'
    'if exist "C:\\Python312\\pythonw.exe" set "PYW=C:\\Python312\\pythonw.exe" & exit /b 0\r\n'
    'if exist "C:\\Python311\\pythonw.exe" set "PYW=C:\\Python311\\pythonw.exe" & exit /b 0\r\n'
    'if exist "%ProgramFiles%\\Python313\\pythonw.exe" set "PYW=%ProgramFiles%\\Python313\\pythonw.exe" & exit /b 0\r\n'
    'if exist "%ProgramFiles%\\Python312\\pythonw.exe" set "PYW=%ProgramFiles%\\Python312\\pythonw.exe" & exit /b 0\r\n'
    'if exist "%LocalAppData%\\Microsoft\\WindowsApps\\pythonw3.13.exe" set "PYW=%LocalAppData%\\Microsoft\\WindowsApps\\pythonw3.13.exe" & exit /b 0\r\n'
    'if exist "%LocalAppData%\\Microsoft\\WindowsApps\\pythonw3.12.exe" set "PYW=%LocalAppData%\\Microsoft\\WindowsApps\\pythonw3.12.exe" & exit /b 0\r\n'
    'if exist "%LocalAppData%\\Microsoft\\WindowsApps\\pythonw3.11.exe" set "PYW=%LocalAppData%\\Microsoft\\WindowsApps\\pythonw3.11.exe" & exit /b 0\r\n'
    'set "_PYWTMP=%TEMP%\\x_auto_dm_pyw_%RANDOM%.txt"\r\n'
    'if "%PY_ARG%"=="" goto :ResolvePywNoArg\r\n'
    '"%PY%" %PY_ARG% -c "import os,re,sys; bx=getattr(sys,\'_base_executable\',\'\') or \'\'; dirs=[os.path.dirname(sys.executable), os.path.dirname(bx) if bx else \'\', sys.exec_prefix or \'\', getattr(sys,\'base_exec_prefix\',\'\'), os.path.join(os.environ.get(\'LOCALAPPDATA\',\'\'),\'Microsoft\',\'WindowsApps\')]; names=[\'pythonw.exe\',\'pythonw3.13.exe\',\'pythonw3.12.exe\',\'pythonw3.11.exe\',\'pythonw3.10.exe\']; sw=re.sub(r\'python(\\d*(?:\\.\\d+)?)\\.exe$\',r\'pythonw\\1.exe\',sys.executable,flags=re.I); hits=([sw] if sw.lower()!=sys.executable.lower() and os.path.lexists(sw) else [])+[os.path.join(d,n) for d in dirs if d for n in names if os.path.lexists(os.path.join(d,n))]; print(hits[0] if hits else sys.executable)" > "%_PYWTMP%" 2>nul\r\n'
    "goto :ReadPywTmp\r\n"
    ":ResolvePywNoArg\r\n"
    '"%PY%" -c "import os,re,sys; bx=getattr(sys,\'_base_executable\',\'\') or \'\'; dirs=[os.path.dirname(sys.executable), os.path.dirname(bx) if bx else \'\', sys.exec_prefix or \'\', getattr(sys,\'base_exec_prefix\',\'\'), os.path.join(os.environ.get(\'LOCALAPPDATA\',\'\'),\'Microsoft\',\'WindowsApps\')]; names=[\'pythonw.exe\',\'pythonw3.13.exe\',\'pythonw3.12.exe\',\'pythonw3.11.exe\',\'pythonw3.10.exe\']; sw=re.sub(r\'python(\\d*(?:\\.\\d+)?)\\.exe$\',r\'pythonw\\1.exe\',sys.executable,flags=re.I); hits=([sw] if sw.lower()!=sys.executable.lower() and os.path.lexists(sw) else [])+[os.path.join(d,n) for d in dirs if d for n in names if os.path.lexists(os.path.join(d,n))]; print(hits[0] if hits else sys.executable)" > "%_PYWTMP%" 2>nul\r\n'
    ":ReadPywTmp\r\n"
    'if exist "%_PYWTMP%" set /p PYW=<"%_PYWTMP%"\r\n'
    'if exist "%_PYWTMP%" del /f /q "%_PYWTMP%" >nul 2>&1\r\n'
    'if not defined PYW set "PYW=%PY%" & set "PYW_ARG=%PY_ARG%"\r\n'
    "exit /b 0\r\n"
    "\r\n"
    ":TryPy\r\n"
    'set "_CAND=%~1"\r\n'
    'set "_CARG=%~2"\r\n'
    'if not "%_CARG%"=="" goto :TryPyArg\r\n'
    '"%_CAND%" -c "import sys, tkinter; sys.exit(0)" >nul 2>&1\r\n'
    'if %errorlevel%==0 set "PY=%_CAND%"\r\n'
    "exit /b 0\r\n"
    ":TryPyArg\r\n"
    '"%_CAND%" %_CARG% -c "import sys, tkinter; sys.exit(0)" >nul 2>&1\r\n'
    "if %errorlevel%==0 goto :TryPySetArg\r\n"
    "exit /b 0\r\n"
    ":TryPySetArg\r\n"
    'set "PY=%_CAND%"\r\n'
    'set "PY_ARG=%_CARG%"\r\n'
    "exit /b 0\r\n"
)

silent_vbs = os.path.join(out_dir, "ツール起動_CMDなし.vbs")

launch_bat = os.path.join(out_dir, "ツール起動.bat")
bypass_bat = os.path.join(out_dir, "ツール起動_ブロック回避版.bat")
debug_bat = os.path.join(out_dir, "【エラー確認用】コマンドプロンプトで起動.bat")

launch_bat_content = (
    "@echo off\r\n"
    "setlocal\r\n"
    "title X Auto DM Tool Pro\r\n"
    'set "BASE_DIR=%~dp0"\r\n'
    'pushd "%BASE_DIR%" >nul 2>&1 || cd /d "%BASE_DIR%"\r\n'
    'if not exist "%BASE_DIR%*.xlsx" goto :NotExtracted\r\n'
    'reg query "HKLM\\SYSTEM\\CurrentControlSet\\Control\\CI\\Policy" /v VerifiedAndReputablePolicyState 2>nul | find "0x1" >nul\r\n'
    "if %errorlevel%==0 goto :LaunchPython\r\n"
    'if not exist "%BASE_DIR%x_auto_dm_gui.exe" goto :LaunchPython\r\n'
    'start "" /D "%BASE_DIR%" "%BASE_DIR%x_auto_dm_gui.exe"\r\n'
    "if errorlevel 1 goto :LaunchPython\r\n"
    "popd >nul 2>&1\r\n"
    "exit /b 0\r\n"
    "\r\n"
    ":NotExtracted\r\n"
    "echo [ERROR] Please extract the ZIP archive first ^(Right-click -^> Extract All^).\r\n"
    "popd >nul 2>&1\r\n"
    "pause\r\n"
    "exit /b 1\r\n"
    "\r\n"
    ":LaunchPython\r\n"
    "echo.\r\n"
    "echo [INFO] Windows Smart App Control / Device Guard blocked the unsigned EXE.\r\n"
    "echo [INFO] Switching to signed Python mode ^(_app\\gui_launcher.py^)...\r\n"
    'if not exist "%BASE_DIR%_app\\gui_launcher.py" goto :NoFallback\r\n'
    "call :FindPython\r\n"
    "if %errorlevel%==0 goto :RunPythonApp\r\n"
    "echo [INFO] Python is not installed yet. Attempting automatic install via winget...\r\n"
    "where winget >nul 2>&1\r\n"
    "if not %errorlevel%==0 goto :OpenStore\r\n"
    "winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements\r\n"
    "call :FindPython\r\n"
    "if %errorlevel%==0 goto :RunPythonApp\r\n"
    ":OpenStore\r\n"
    "echo [ACTION REQUIRED] Please install Python 3.12 from Microsoft Store ^(official signed runtime^),\r\n"
    "echo then double-click this batch file again.\r\n"
    "start ms-windows-store://pdp/?productid=9NCVDN91XZQP\r\n"
    "popd >nul 2>&1\r\n"
    "pause\r\n"
    "exit /b 1\r\n"
    "\r\n"
    ":RunPythonApp\r\n"
    '"%PY%" %PY_ARG% -c "import openpyxl, playwright" >nul 2>&1\r\n'
    "if errorlevel 1 goto :InstallDeps\r\n"
    "goto :StartGuiPy\r\n"
    ":InstallDeps\r\n"
    "echo [INFO] Installing required Python libraries ^(openpyxl, playwright^)...\r\n"
    '"%PY%" %PY_ARG% -m pip install -r "%BASE_DIR%_app\\requirements.txt"\r\n'
    "if errorlevel 1 goto :PipFailed\r\n"
    ":StartGuiPy\r\n"
    'set "X_AUTO_DM_BASE_DIR=%BASE_DIR%"\r\n'
    'if exist "%BASE_DIR%_internal\\ms-playwright" set "PLAYWRIGHT_BROWSERS_PATH=%BASE_DIR%_internal\\ms-playwright"\r\n'
    'start "" /D "%BASE_DIR%" "%PYW%" %PYW_ARG% "%BASE_DIR%_app\\gui_launcher.py"\r\n'
    "popd >nul 2>&1\r\n"
    "exit /b 0\r\n"
    "\r\n"
    ":PipFailed\r\n"
    "echo [ERROR] pip install failed. Check internet connection and try again.\r\n"
    "popd >nul 2>&1\r\n"
    "pause\r\n"
    "exit /b 1\r\n"
    "\r\n"
    ":NoFallback\r\n"
    "echo [ERROR] Neither x_auto_dm_gui.exe nor _app\\gui_launcher.py could be started.\r\n"
    "popd >nul 2>&1\r\n"
    "pause\r\n"
    "exit /b 1\r\n"
    "\r\n"
    + PYTHON_FINDER_SUBS
)

bypass_bat_content = (
    "@echo off\r\n"
    "setlocal\r\n"
    "title X Auto DM Tool Pro - Smart App Control Safe Mode\r\n"
    'set "BASE_DIR=%~dp0"\r\n'
    'pushd "%BASE_DIR%" >nul 2>&1 || cd /d "%BASE_DIR%"\r\n'
    'if not exist "%BASE_DIR%_app\\gui_launcher.py" goto :MissingApp\r\n'
    "call :FindPython\r\n"
    "if %errorlevel%==0 goto :CheckDeps\r\n"
    "echo [INFO] Python 3.12 not found. Attempting automatic install via winget...\r\n"
    "where winget >nul 2>&1\r\n"
    "if not %errorlevel%==0 goto :OpenStore\r\n"
    "winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements\r\n"
    "call :FindPython\r\n"
    "if %errorlevel%==0 goto :CheckDeps\r\n"
    ":OpenStore\r\n"
    "echo [ERROR] Please install official signed Python 3.12 from Microsoft Store and retry.\r\n"
    "start ms-windows-store://pdp/?productid=9NCVDN91XZQP\r\n"
    "popd >nul 2>&1\r\n"
    "pause\r\n"
    "exit /b 1\r\n"
    "\r\n"
    ":CheckDeps\r\n"
    '"%PY%" %PY_ARG% -c "import openpyxl, playwright" >nul 2>&1\r\n'
    "if errorlevel 1 goto :InstallDeps\r\n"
    "goto :LaunchPy\r\n"
    ":InstallDeps\r\n"
    "echo [INFO] Installing openpyxl and playwright...\r\n"
    '"%PY%" %PY_ARG% -m pip install -r "%BASE_DIR%_app\\requirements.txt"\r\n'
    "if errorlevel 1 goto :PipErr\r\n"
    ":LaunchPy\r\n"
    'set "X_AUTO_DM_BASE_DIR=%BASE_DIR%"\r\n'
    'if exist "%BASE_DIR%_internal\\ms-playwright" set "PLAYWRIGHT_BROWSERS_PATH=%BASE_DIR%_internal\\ms-playwright"\r\n'
    'start "" /D "%BASE_DIR%" "%PYW%" %PYW_ARG% "%BASE_DIR%_app\\gui_launcher.py"\r\n'
    "popd >nul 2>&1\r\n"
    "exit /b 0\r\n"
    "\r\n"
    ":PipErr\r\n"
    "echo [ERROR] Failed to install dependencies.\r\n"
    "popd >nul 2>&1\r\n"
    "pause\r\n"
    "exit /b 1\r\n"
    "\r\n"
    ":MissingApp\r\n"
    "echo [ERROR] _app\\gui_launcher.py not found. Please extract the ZIP first.\r\n"
    "popd >nul 2>&1\r\n"
    "pause\r\n"
    "exit /b 1\r\n"
    "\r\n"
    + PYTHON_FINDER_SUBS
)

debug_bat_content = (
    "@echo off\r\n"
    "setlocal\r\n"
    "chcp 65001 >nul\r\n"
    "set PYTHONIOENCODING=utf-8\r\n"
    "set PYTHONUTF8=1\r\n"
    "title X Auto DM Tool Pro Debug\r\n"
    'set "BASE_DIR=%~dp0"\r\n'
    'pushd "%BASE_DIR%" >nul 2>&1 || cd /d "%BASE_DIR%"\r\n'
    "echo ========================================================\r\n"
    "echo   X Auto DM Tool Pro - Launch Diagnostics\r\n"
    "echo ========================================================\r\n"
    'echo Working Dir: "%BASE_DIR%"\r\n'
    'if exist "%BASE_DIR%launch_error.log" del /f /q "%BASE_DIR%launch_error.log" >nul 2>&1\r\n'
    'reg query "HKLM\\SYSTEM\\CurrentControlSet\\Control\\CI\\Policy" /v VerifiedAndReputablePolicyState 2>nul | find "0x1" >nul\r\n'
    "if %errorlevel%==0 goto :DebugPython\r\n"
    'if not exist "%BASE_DIR%x_auto_dm_gui.exe" goto :DebugPython\r\n'
    "echo Testing x_auto_dm_gui.exe...\r\n"
    'start /wait "" /D "%BASE_DIR%" "%BASE_DIR%x_auto_dm_gui.exe"\r\n'
    "set EXIT_RC=%errorlevel%\r\n"
    "echo Process exited with code: %EXIT_RC%\r\n"
    "if not %EXIT_RC%==0 goto :DebugPython\r\n"
    'if exist "%BASE_DIR%launch_error.log" goto :ShowErrLog\r\n'
    "goto :EndDebug\r\n"
    "\r\n"
    ":DebugPython\r\n"
    "echo [INFO] Running signed Python diagnostic via _app\\gui_launcher.py...\r\n"
    "call :FindPython\r\n"
    "if not %errorlevel%==0 goto :NoPyDebug\r\n"
    'echo Using Python: %PY% %PY_ARG%\r\n'
    'set "X_AUTO_DM_BASE_DIR=%BASE_DIR%"\r\n'
    'if exist "%BASE_DIR%_internal\\ms-playwright" set "PLAYWRIGHT_BROWSERS_PATH=%BASE_DIR%_internal\\ms-playwright"\r\n'
    '"%PY%" %PY_ARG% "%BASE_DIR%_app\\gui_launcher.py" --keep-console\r\n'
    "echo Python mode exited with code: %errorlevel%\r\n"
    'if exist "%BASE_DIR%launch_error.log" goto :ShowErrLog\r\n'
    "goto :EndDebug\r\n"
    "\r\n"
    ":NoPyDebug\r\n"
    "echo [ERROR] Python 3 with tkinter was not found on this PC.\r\n"
    "goto :EndDebug\r\n"
    "\r\n"
    ":ShowErrLog\r\n"
    "echo --------------------------------------------------------\r\n"
    "echo [launch_error.log]\r\n"
    'type "%BASE_DIR%launch_error.log"\r\n'
    "echo --------------------------------------------------------\r\n"
    ":EndDebug\r\n"
    "popd >nul 2>&1\r\n"
    "pause\r\n"
    "exit /b 0\r\n"
    "\r\n"
    + PYTHON_FINDER_SUBS
)

silent_vbs_content = (
    "' Zero-CMD GUI Launcher for X Auto DM Tool Pro\r\n"
    "Option Explicit\r\n"
    "Dim WshShell, fso, baseDir, exePath, pyGuiPath, pwPath\r\n"
    'Set WshShell = CreateObject("WScript.Shell")\r\n'
    'Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
    "baseDir = fso.GetParentFolderName(WScript.ScriptFullName)\r\n"
    "WshShell.CurrentDirectory = baseDir\r\n"
    'exePath = fso.BuildPath(baseDir, "x_auto_dm_gui.exe")\r\n'
    'pyGuiPath = fso.BuildPath(fso.BuildPath(baseDir, "_app"), "gui_launcher.py")\r\n'
    'pwPath = fso.BuildPath(fso.BuildPath(baseDir, "_internal"), "ms-playwright")\r\n'
    "\r\n"
    "Dim sacVal\r\n"
    "sacVal = 0\r\n"
    "On Error Resume Next\r\n"
    'sacVal = WshShell.RegRead("HKLM\\SYSTEM\\CurrentControlSet\\Control\\CI\\Policy\\VerifiedAndReputablePolicyState")\r\n'
    "If Err.Number <> 0 Then\r\n"
    "    sacVal = 0\r\n"
    "    Err.Clear\r\n"
    "End If\r\n"
    "On Error GoTo 0\r\n"
    "\r\n"
    "If sacVal <> 1 And fso.FileExists(exePath) Then\r\n"
    "    On Error Resume Next\r\n"
    '    WshShell.Run """" & exePath & """", 1, False\r\n'
    "    If Err.Number = 0 Then\r\n"
    "        WScript.Quit 0\r\n"
    "    End If\r\n"
    "    Err.Clear\r\n"
    "    On Error GoTo 0\r\n"
    "End If\r\n"
    "\r\n"
    "If fso.FileExists(pyGuiPath) Then\r\n"
    "    Dim localApp, progFiles, pywExe, candidates, c\r\n"
    '    localApp = WshShell.ExpandEnvironmentStrings("%LocalAppData%")\r\n'
    '    progFiles = WshShell.ExpandEnvironmentStrings("%ProgramFiles%")\r\n'
    '    pywExe = ""\r\n'
    "    candidates = Array( _\r\n"
    '        baseDir & "\\.venv\\Scripts\\pythonw.exe", _\r\n'
    '        baseDir & "\\venv\\Scripts\\pythonw.exe", _\r\n'
    '        localApp & "\\Programs\\Python\\Python313\\pythonw.exe", _\r\n'
    '        localApp & "\\Programs\\Python\\Python312\\pythonw.exe", _\r\n'
    '        localApp & "\\Programs\\Python\\Python311\\pythonw.exe", _\r\n'
    '        localApp & "\\Programs\\Python\\Python310\\pythonw.exe", _\r\n'
    '        "C:\\Python313\\pythonw.exe", _\r\n'
    '        "C:\\Python312\\pythonw.exe", _\r\n'
    '        "C:\\Python311\\pythonw.exe", _\r\n'
    '        progFiles & "\\Python313\\pythonw.exe", _\r\n'
    '        progFiles & "\\Python312\\pythonw.exe", _\r\n'
    '        localApp & "\\Microsoft\\WindowsApps\\pythonw3.14.exe", _\r\n'
    '        localApp & "\\Microsoft\\WindowsApps\\pythonw3.13.exe", _\r\n'
    '        localApp & "\\Microsoft\\WindowsApps\\pythonw3.12.exe", _\r\n'
    '        localApp & "\\Microsoft\\WindowsApps\\pythonw3.11.exe", _\r\n'
    '        localApp & "\\Microsoft\\WindowsApps\\pythonw3.10.exe" _\r\n'
    "    )\r\n"
    "    For Each c In candidates\r\n"
    "        If fso.FileExists(c) Then\r\n"
    "            Dim rcChk\r\n"
    "            On Error Resume Next\r\n"
    '            rcChk = WshShell.Run("""" & c & """ -c ""import tkinter, openpyxl, playwright""", 0, True)\r\n'
    "            If Err.Number = 0 And rcChk = 0 Then\r\n"
    "                pywExe = c\r\n"
    "                Exit For\r\n"
    "            End If\r\n"
    "            Err.Clear\r\n"
    "            On Error GoTo 0\r\n"
    "        End If\r\n"
    "    Next\r\n"
    '    If pywExe = "" Then\r\n'
    "        On Error Resume Next\r\n"
    '        If WshShell.Run("pyw -c ""import tkinter, openpyxl, playwright""", 0, True) = 0 Then pywExe = "pyw"\r\n'
    "        If Err.Number <> 0 Then Err.Clear\r\n"
    '        If pywExe = "" Then\r\n'
    '            If WshShell.Run("pythonw -c ""import tkinter, openpyxl, playwright""", 0, True) = 0 Then pywExe = "pythonw"\r\n'
    "            If Err.Number <> 0 Then Err.Clear\r\n"
    "        End If\r\n"
    "        On Error GoTo 0\r\n"
    "    End If\r\n"
    '    If pywExe <> "" Then\r\n'
    "        Dim procEnv\r\n"
    '        Set procEnv = WshShell.Environment("Process")\r\n'
    '        procEnv("X_AUTO_DM_BASE_DIR") = baseDir\r\n'
    "        If fso.FolderExists(pwPath) Then\r\n"
    '            procEnv("PLAYWRIGHT_BROWSERS_PATH") = pwPath\r\n'
    "        End If\r\n"
    '        WshShell.Run """" & pywExe & """ """ & pyGuiPath & """", 0, False\r\n'
    "        WScript.Quit 0\r\n"
    "    End If\r\n"
    "End If\r\n"
    "\r\n"
    "Dim fallbackBat\r\n"
    'fallbackBat = fso.BuildPath(baseDir, "ツール起動_ブロック回避版.bat")\r\n'
    "If fso.FileExists(fallbackBat) Then\r\n"
    '    WshShell.Run """" & fallbackBat & """", 1, False\r\n'
    "End If\r\n"
)

with open(launch_bat, "wb") as f:
    f.write(launch_bat_content.encode("ascii"))
with open(bypass_bat, "wb") as f:
    f.write(bypass_bat_content.encode("ascii"))
with open(debug_bat, "wb") as f:
    f.write(debug_bat_content.encode("ascii"))
with open(silent_vbs, "w", encoding="utf-16", newline="") as f:
    f.write(silent_vbs_content)

# (E) かんたんガイドテキスト & BUILD_INFO.txt (最新版ビルド証明)
build_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
sac_enforced = is_smart_app_control_enforced()
readme_txt = os.path.join(out_dir, "README_他PCでの使い方.txt")
with open(readme_txt, "w", encoding="utf-8") as f:
    f.write(f"""======================================================================
  X (Twitter) 自動DM・リプライ・いいねツール Pro - 他PC用かんたんガイド
  ビルド日時: {build_ts}
======================================================================

【このPCでのご利用手順（たったの2ステップ）】

1. 「アカウント情報.xlsx」と「アカウント名.xlsx」(または アカウントID.xlsx) を開き、
   アカウント設定やターゲット・メッセージを入力して保存します。

2. 以下のいずれかをダブルクリックしてアプリ画面だけを起動！
   ・ 「ツール起動_CMDなし.vbs」 ← 黒いCMD画面が一切出ずにアプリ画面だけ開きます（推奨）
   ・ 「ツール起動.bat」         ← 初回セットアップや自動判定が必要な場合用（起動後CMDは自動で閉じます）
   ・ 「x_auto_dm_gui.exe」      ← スマート アプリ コントロール無効環境ではこれ単体で起動可能
   ※ ZIPのまま中を開かず、必ず右クリック「すべて展開」したフォルダ内で起動してください。
   ※ 手動ログイン用ブラウザもGUI内の「👤 Chromeプロファイル起動」ボタンから直接使えます。

【スマート アプリ コントロール (Device Guard) でEXEがブロックされる場合】
  Windows 11 の「スマート アプリ コントロール」が有効なPCでは、証明書のない
  自作EXE (x_auto_dm_gui.exe) の直接実行がWindows OSによりブロックされます。
  その場合は「ツール起動_CMDなし.vbs」または「ツール起動.bat」を実行してください
  (自動的に署名済み pythonw.exe 経由に切り替わり、黒いCMD画面なしでアプリだけ起動します)。
  ※ Python未導入の他PCでも、「ツール起動.bat」が公式署名付き Python 3.12 を自動案内して動作します。

【フォルダ構成】
  ・ スクショフォルダ/  … ログイン失敗・送信失敗・例外時の画面スクショ(PNG)を自動保存
  ・ logフォルダ/       … アカウントごとの実行ログ (.log)
  ・ dbフォルダ/        … 重複送信防止SQLite3データベース (.db)
  ・ browser_profiles/  … ログイン済みブラウザセッション

※ このフォルダにはPython実行環境・Chromiumブラウザ・依存モジュールが同梱されています。
※ 万一起動しない場合は「【エラー確認用】コマンドプロンプトで起動.bat」で原因を確認できます。
======================================================================
""")

build_info_path = os.path.join(out_dir, "BUILD_INFO.txt")
with open(build_info_path, "w", encoding="utf-8") as f:
    f.write(f"build_timestamp={build_ts}\n")
    f.write(f"python_version={sys.version.split()[0]}\n")
    f.write(f"pyinstaller_version={getattr(PyInstaller, '__version__', 'unknown')}\n")
    f.write(f"smart_app_control_enforced={sac_enforced}\n")
    f.write(f"bundled_browsers={','.join(bundled_browsers) if bundled_browsers else 'none'}\n")
    f.write("sources:\n")
    for py_name in core_py_files:
        py_path = os.path.join(base_dir, py_name)
        mtime_str = datetime.datetime.fromtimestamp(os.path.getmtime(py_path)).strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"  - {py_name}: {mtime_str} ({os.path.getsize(py_path)} bytes)\n")

# (F) トップレベル配下の不要一時ファイルのみ除去 (※ _internal / _app 内部は削除しない！)
for fname in list(os.listdir(out_dir)):
    fpath = os.path.join(out_dir, fname)
    fl = fname.lower()
    if os.path.isfile(fpath):
        if fl.endswith((".py", ".pyw", ".pyc", ".pyo", ".spec", ".log")) or fname in (".pause", ".stop"):
            os.remove(fpath)
    elif os.path.isdir(fpath) and fname == "__pycache__":
        shutil.rmtree(fpath, ignore_errors=True)

# ビルド中間キャッシュも掃除
for tmp_dir in (build_tmp, dist_src):
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)

# 5. 他PC転送用のZIPファイルを自動生成 (一時ZIP作成 ➜ 整合性検証 ➜ 置換)
print("\n[5/5] 他PC転送用ZIPファイルを作成中...")
zip_dest = os.path.join(base_dir, "【他PC用】x_auto_dm_tool_完全版.zip")
zip_tmp = zip_dest + ".tmp"
if os.path.exists(zip_tmp):
    try:
        os.remove(zip_tmp)
    except Exception as e:
        print(f"❌ 一時ZIPファイルを削除できません: {e}")
        sys.exit(1)

try:
    with zipfile.ZipFile(zip_tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(out_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, dist_root)
                zf.write(full_path, rel_path)

    with zipfile.ZipFile(zip_tmp, "r") as zf:
        names = set(zf.namelist())
        has_exe = any(n.endswith("/x_auto_dm_gui.exe") for n in names)
        has_bat = any(n.endswith("/ツール起動.bat") for n in names)
        has_vbs = any(n.endswith("/ツール起動_CMDなし.vbs") for n in names)
        has_app = any(n.endswith("/_app/gui_launcher.py") for n in names)
        has_ss = any("/スクショフォルダ/" in n for n in names)
        if not (has_exe and has_bat and has_vbs and has_app and has_ss):
            raise RuntimeError("ZIP内に必要ファイル (x_auto_dm_gui.exe / ツール起動.bat / ツール起動_CMDなし.vbs / _app/gui_launcher.py / スクショフォルダ) が不足しています。")

    os.replace(zip_tmp, zip_dest)
    print(f"   ✅ 最新ZIP圧縮が完了しました (サイズ: {os.path.getsize(zip_dest):,} bytes)")
except Exception as e:
    if os.path.exists(zip_tmp):
        try:
            os.remove(zip_tmp)
        except Exception:
            pass
    print(f"❌ ZIPファイルの作成・上書きに失敗しました: {e}")
    print("   ※ 古いZIPファイルを開いたままにしていないか確認してください。")
    sys.exit(1)

print("\n" + "=" * 70)
print(f"  🎉 【他PC配布用パッケージの作成が完了しました！】 ({build_ts})")
print("=" * 70)
print(f"\n📦 生成された最新ZIPファイル:\n   👉 {zip_dest}")
print(f"\n📁 生成された最新配布フォルダ:\n   👉 {out_dir}")
if sac_enforced:
    print("\n⚠️ 【検出】このPCは「スマート アプリ コントロール (Device Guard)」が有効です。")
    print("   ・未署名の x_auto_dm_gui.exe 直接起動は Windows OS によりブロックされます。")
    print("   ・代わりに「ツール起動_CMDなし.vbs」または「ツール起動.bat」を実行すると")
    print("     黒いCMD画面を出さず、署名済み pythonw 経由でアプリ画面だけを起動できます！")
print(r"""
----------------------------------------------------------------------
📋 【他PC配布フォルダ内のファイル構成】
   ├── ツール起動_CMDなし.vbs                 ← ★黒いCMDゼロ！アプリ画面だけ即起動(推奨)
   ├── ツール起動.bat                         ← 自動判定・初回ライブラリ自動導入対応
   ├── ツール起動_ブロック回避版.bat          ← Device Guard有効PC向けの直接起動
   ├── 【エラー確認用】コマンドプロンプトで起動.bat ← トラブル確認用
   ├── x_auto_dm_gui.exe                      ← 単一EXE (SAC無効PC / 署名済み環境用)
   ├── BUILD_INFO.txt                         ← ビルド日時・収録ファイル情報
   ├── アカウント情報.xlsx / アカウント名.xlsx
   ├── スクショフォルダ/                      ← エラー発生時スクショPNG自動保存先
   ├── logフォルダ/ / dbフォルダ/ / browser_profiles/
   ├── _app/                                  ← Device Guard回避用 最新Pythonランタイム群
   └── _internal/                             ← Python本体・Chromium・依存モジュール同梱
----------------------------------------------------------------------
""")

if sys.platform == "win32":
    try:
        subprocess.run(["explorer", f"/select,{zip_dest}"])
    except Exception:
        pass
