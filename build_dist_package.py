"""
他PC配布用パッケージ自動作成スクリプト (build_dist_package.py)
このPCでPythonコードをEXEコンパイルし、Playwright Chromiumブラウザを完全同梱して、
他PCではZIP解凍だけで何もセットアップせずに即座に実行できる「超シンプル構成」の完全版パッケージを作成します。
"""

import os
import sys
import shutil
import zipfile
import subprocess

base_dir = os.path.dirname(os.path.abspath(__file__))

print("=" * 70)
print("  📦 他PC配布用パッケージ自動作成 (Python・ブラウザ完全同梱コンパイル)")
print("  ★ このPCでコンパイル実行 ➜ 他PCではZIP解凍だけで即座に動く完全版を作成")
print("=" * 70)

# 1. 依存ライブラリの自動確認 & インストール
print("\n[1/5] PyInstaller および必要パッケージの確認...")
try:
    subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller", "openpyxl", "playwright", "--quiet"], check=True)
    print("   ✅ 必要ライブラリの確認完了")
except Exception as e:
    print(f"⚠️ pip インストール警告: {e}")

# 2. Playwright Chromium ブラウザの確認・ダウンロード
print("\n[2/5] Playwright Chromium ブラウザの確認・準備...")
try:
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    print("   ✅ Playwright Chromium ブラウザの準備完了")
except Exception as e:
    print(f"⚠️ Playwright ブラウザ準備警告: {e}")

# 3. PyInstaller によるEXE化コンパイル
print("\n[3/5] Pythonコードを単一EXE化コンパイル中 (PyInstaller)...")
spec_path = os.path.join(base_dir, "x_auto_dm.spec")
res = subprocess.run([sys.executable, "-m", "PyInstaller", spec_path, "--noconfirm", "--clean"], cwd=base_dir)
if res.returncode != 0:
    print("❌ PyInstaller コンパイルに失敗しました。")
    sys.exit(1)
print("   ✅ EXEコンパイル完了 (x_auto_dm_gui.exe)")

# 4. 超シンプル構成の他PC配布用フォルダを作成
print("\n[4/5] 他PC配布用フォルダを整理中 (ファイル構成を超シンプルに最小化)...")
out_dir = os.path.join(base_dir, "dist", "【他PC用】x_auto_dm_tool")
if os.path.exists(out_dir):
    try:
        shutil.rmtree(out_dir, ignore_errors=True)
    except Exception:
        pass
os.makedirs(out_dir, exist_ok=True)

# (A) PyInstaller出力物 (EXE本体および_internal) をコピー
dist_src = os.path.join(base_dir, "dist", "x_auto_dm_tool")
if os.path.exists(dist_src):
    for item in os.listdir(dist_src):
        s = os.path.join(dist_src, item)
        d = os.path.join(out_dir, item)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)

# (A-2) profile_luncher.exe も同名で複製
if os.path.exists(os.path.join(out_dir, "profile_launcher.exe")):
    shutil.copy2(os.path.join(out_dir, "profile_launcher.exe"), os.path.join(out_dir, "profile_luncher.exe"))

# (A-3) プロファイル起動バッチを他PC配布フォルダに同梱
for bname in ["03_プロファイル起動_profile_launcher.bat", "profile_launcher.bat", "profile_luncher.bat"]:
    bpath = os.path.join(base_dir, bname)
    if os.path.exists(bpath):
        shutil.copy2(bpath, os.path.join(out_dir, bname))

# (B) Playwright Chromiumブラウザを同梱 (_internal/ms-playwright 内に完全格納しルートはシンプルに保持)
print("   ・Playwright Chromiumブラウザ本体を同梱中 (他PCダウンロード不要)...")
local_appdata = os.environ.get("LOCALAPPDATA", "")
user_profile = os.environ.get("USERPROFILE", "")
pw_sources = [
    os.path.join(local_appdata, "ms-playwright"),
    os.path.join(user_profile, "AppData", "Local", "ms-playwright"),
    os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
]
found_pw_src = ""
for p in pw_sources:
    if p and os.path.isdir(p):
        found_pw_src = p
        break

if found_pw_src:
    try:
        target_pw = os.path.join(out_dir, "_internal", "ms-playwright")
        os.makedirs(target_pw, exist_ok=True)
        for item in os.listdir(found_pw_src):
            s = os.path.join(found_pw_src, item)
            d = os.path.join(target_pw, item)
            if os.path.isdir(s):
                # Chromiumとffmpegのみ同梱し、不要なFirefox/WebKitは除外して軽量化
                if item.startswith("chromium-") or item.startswith("ffmpeg-"):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                elif not (item.startswith("firefox-") or item.startswith("webkit-")):
                    shutil.copytree(s, d, dirs_exist_ok=True)
        print("   ✅ Playwright Chromiumブラウザの同梱完了 (_internal/ms-playwright)")
    except Exception as e:
        print(f"⚠️ ブラウザ同梱警告: {e}")
else:
    print("⚠️ ms-playwright フォルダが見つかりませんでした。")

# (C) ユーザー設定用Excelファイルをコピー (アカウント情報.xlsx, アカウント名.xlsx, 各アカウント専用.xlsxなど全Excel)
for fname in os.listdir(base_dir):
    if fname.endswith(".xlsx") and not fname.startswith("~$"):
        fpath = os.path.join(base_dir, fname)
        if os.path.isfile(fpath):
            shutil.copy2(fpath, os.path.join(out_dir, fname))
print("   ✅ 設定用Excelファイルの同梱完了")

# (C-2) logフォルダ および dbフォルダの作成 (実行時にアカウント名.log / アカウント名.DB が自動保存される)
log_dist = os.path.join(out_dir, "logフォルダ")
db_dist = os.path.join(out_dir, "dbフォルダ")
profiles_dist = os.path.join(out_dir, "browser_profiles")
os.makedirs(log_dist, exist_ok=True)
os.makedirs(db_dist, exist_ok=True)
os.makedirs(profiles_dist, exist_ok=True)

try:
    with open(os.path.join(log_dist, "README_ログについて.txt"), "w", encoding="utf-8") as f:
        f.write("実行されたアカウントごとに、ここに「アカウント名.log」（例: wqwq1320.log）が自動生成されます。\n")
    with open(os.path.join(db_dist, "README_データベースについて.txt"), "w", encoding="utf-8") as f:
        f.write("実行されたアカウントごとに、ここに「アカウント名.DB」（例: wqwq1320.DB / 重複防止SQLite3）が自動生成されます。\n")
except Exception:
    pass

# (D) 他PC用起動バッチ (超シンプル: ダブルクリックするだけ + 未展開チェック)
launch_bat = os.path.join(out_dir, "🚀 ツール起動.bat")
debug_bat = os.path.join(out_dir, "【エラー確認用】コマンドプロンプトで起動.bat")
try:
    with open(launch_bat, "w", encoding="cp932", errors="replace") as f:
        f.write('@echo off\r\nchcp 65001 >nul\r\ntitle X Auto DM Tool Pro\r\ncd /d "%~dp0"\r\nif not exist "x_auto_dm_gui.exe" (\r\n    echo [エラー] x_auto_dm_gui.exe が見つかりません。ZIPを「すべて展開」してください。\r\n    pause\r\n    exit /b 1\r\n)\r\nstart "" "x_auto_dm_gui.exe"\r\nexit\r\n')
    with open(debug_bat, "w", encoding="cp932", errors="replace") as f:
        f.write('@echo off\r\nchcp 65001 >nul\r\ntitle X Auto DM Tool Pro Debug\r\ncd /d "%~dp0"\r\necho ========================================================\r\necho   X 自動DMツール (エラー確認・診断コンソール起動)\r\necho   ※ 画面が閉じずにエラーログが残ります\r\necho ========================================================\r\necho.\r\nx_auto_dm_gui.exe\r\necho.\r\necho ========================================================\r\necho   アプリが終了しました。キーを押すと画面を閉じます。\r\necho ========================================================\r\npause\r\n')
except Exception:
    pass

# (E) 超シンプルな使い方テキスト
readme_txt = os.path.join(out_dir, "README_他PCでの使い方.txt")
try:
    with open(readme_txt, "w", encoding="utf-8") as f:
        f.write("""======================================================================
  X (Twitter) 自動DM・リプライ・いいねツール Pro - 他PC用かんたんガイド
======================================================================

【このPCでのご利用手順（たったの2ステップ）】

1. 「アカウント情報.xlsx」と「アカウント名.xlsx」を開き、
   あなたのアカウント設定（auth_token等）やターゲット、メッセージを入力して保存します。

2. 「🚀 ツール起動.bat」または「x_auto_dm_gui.exe」をダブルクリック！

※ このフォルダにはPython実行環境・Chromiumブラウザ・全ライブラリが完全に同梱されています。
※ 他のPC側でのPythonインストールや環境構築、コマンド実行は【一切不要】です！
※ 解凍したフォルダのまま、USBメモリや共有ドライブ経由で即座に動作します。
======================================================================
""")
except Exception:
    pass

# (F) 他PC配布用フォルダ内の不要ファイルを徹底クリーンアップ (ルートは超シンプルに保つ)
for root, dirs, files in os.walk(out_dir):
    for f in files:
        if f.endswith(".pyc") or f == ".pause" or f.endswith(".spec"):
            try:
                os.remove(os.path.join(root, f))
            except Exception:
                pass
    for d in list(dirs):
        if d == "__pycache__":
            try:
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
            except Exception:
                pass

# 5. 他PC転送用のZIPファイルを自動生成
print("\n[5/5] 他PC転送用ZIPファイルを作成中...")
zip_dest = os.path.join(base_dir, "【他PC用】x_auto_dm_tool_完全版.zip")
if os.path.exists(zip_dest):
    try:
        os.remove(zip_dest)
    except Exception:
        pass

try:
    with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(out_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, os.path.join(base_dir, "dist"))
                zf.write(full_path, rel_path)
    print("   ✅ ZIP圧縮が完了しました。")
except Exception as e:
    print(f"⚠️ ZIP圧縮警告: {e}")

print("\n" + "=" * 70)
print("  🎉 【他PC配布用パッケージの作成が完了しました！】")
print("=" * 70)
print(f"\n📦 生成された転送用ZIPファイル:\n   👉 {zip_dest}")
print(f"\n📁 生成された配布フォルダ:\n   👉 {out_dir}")
print("""
----------------------------------------------------------------------
📋 【他PC配布フォルダ内のファイル構成（超シンプル・たったの4ファイル+_internal）】
   ├── 🚀 ツール起動.bat        ← 他PCではこれをダブルクリックするだけ！
   ├── x_auto_dm_gui.exe       ← アプリ本体 (直接起動も可能)
   ├── アカウント情報.xlsx     ← アカウント設定
   ├── アカウント名.xlsx       ← ターゲット・メッセージ設定
   ├── README_他PCでの使い方.txt
   └── _internal\              ← Python本体・ブラウザ(Chromium)・全依存ファイル完全同梱
----------------------------------------------------------------------

💡 【他PCへの移行方法】:
   上記で作成された「【他PC用】x_auto_dm_tool_完全版.zip」を
   USBメモリやGoogle Drive等で別のPCへコピーし、右クリック「すべて展開」して
   「🚀 ツール起動.bat」をダブルクリックするだけで即座に完全動作します！
   （※他PCでのPythonインストール・pip実行は一切不要です）
""")

if sys.platform == "win32":
    try:
        subprocess.run(["explorer", f"/select,{zip_dest}"])
    except Exception:
        pass
