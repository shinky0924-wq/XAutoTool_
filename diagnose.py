"""
環境診断 & 動作確認スクリプト (diagnose.py)
Pythonから直接実行されるため、Shift-JISや文字化けの影響を受けず、
日本語で明確に診断結果と解決策を表示します。
"""
import sys
import os
import traceback

base_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(base_dir)

print("=" * 65)
print("   🔍 X (Twitter) 自動DMツール - 環境診断 & 起動テスト")
print("   ※ 画面は閉じずに、診断結果がすべてここに表示されます")
print("=" * 65)

# [診断 1] カレントディレクトリ & 解凍チェック
print()
print("[診断 1/5] 実行フォルダの確認:")
print(f"   現在のパス: {base_dir}")

acc_info = os.path.join(base_dir, "アカウント情報.xlsx")
acc_name = os.path.join(base_dir, "アカウント名.xlsx")
if os.path.exists(acc_info):
    print("   ✅ アカウント情報.xlsx: 検出OK")
else:
    print("   ❌ アカウント情報.xlsx: 見つかりません！（ZIPを「すべて展開」してください）")

if os.path.exists(acc_name):
    print("   ✅ アカウント名.xlsx: 検出OK")
else:
    print("   ❌ アカウント名.xlsx: 見つかりません！")

# [診断 2] Python バージョン
print()
print("[診断 2/5] Python環境の検出:")
print(f"   Pythonバージョン: {sys.version.split()[0]}")
print(f"   実行パス: {sys.executable}")

# [診断 3] 必須ライブラリ
print()
print("[診断 3/5] 必須ライブラリの読み込み確認:")
for mod in ["openpyxl", "playwright", "tkinter", "sqlite3"]:
    try:
        __import__(mod)
        print(f"   ✅ {mod}: 正常にインポート成功")
    except Exception as e:
        print(f"   ❌ {mod}: インポート失敗 ({e})")

# [診断 4] フォルダ構造
print()
print("[診断 4/5] データベース・ログフォルダの確認:")
for fdir in ["dbフォルダ", "logフォルダ"]:
    p = os.path.join(base_dir, fdir)
    if os.path.exists(p):
        print(f"   ✅ {fdir}: 存在OK")
    else:
        os.makedirs(p, exist_ok=True)
        print(f"   ✅ {fdir}: 新規作成完了")

# [診断 5] モジュール読み込みテスト
print()
print("[診断 5/5] アプリケーション本体モジュールのテスト:")
try:
    import config_manager
    print("   ✅ config_manager: 正常")
except Exception as e:
    print(f"   ❌ config_manager 読込エラー: {e}")

try:
    import db_manager
    print(f"   ✅ db_manager: 正常 (DBフォルダ: {db_manager.get_db_dir(base_dir)})")
except Exception as e:
    print(f"   ❌ db_manager 読込エラー: {e}")

try:
    import gui_launcher
    print("   ✅ gui_launcher: 正常 (GUI初期化可能)")
except Exception as e:
    print(f"   ❌ gui_launcher 読込エラー: {e}")

# [診断 6] ブラウザ環境 (Google Chrome / Microsoft Edge / Chromium)
print()
print("[診断 6/6] ブラウザ環境の確認:")
chrome_installed = any(os.path.isfile(os.path.expandvars(p)) for p in [
    r"%ProgramFiles%GoogleChromeApplicationchrome.exe",
    r"%ProgramFiles(x86)%GoogleChromeApplicationchrome.exe",
    os.path.expandvars(r"%LocalAppData%GoogleChromeApplicationchrome.exe"),
])
edge_installed = any(os.path.isfile(os.path.expandvars(p)) for p in [
    r"%ProgramFiles(x86)%MicrosoftEdgeApplicationmsedge.exe",
    r"%ProgramFiles%MicrosoftEdgeApplicationmsedge.exe",
])
if chrome_installed:
    print("   ✅ Google Chrome: 検出OK (推奨: 自動連携で最も安定・安全に動作)")
if edge_installed:
    print("   ✅ Microsoft Edge: 検出OK (Windows標準搭載: 署名済みでセキュリティソフトにブロックされません)")
if not chrome_installed and not edge_installed:
    print("   ℹ️ Google Chrome / Edge が検出されませんでした。Playwright同梱Chromiumを使用します。")

print()
print("=" * 65)
print("   🎉 診断が完了しました！")
print("   上記に「❌」がなければ「01_このPCでツール起動.bat」で即起動できます。")
print("=" * 65)
try:
    input("\nEnterキーを押すと終了します...")
except Exception:
    pass
