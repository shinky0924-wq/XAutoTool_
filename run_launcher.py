"""
X (Twitter) 自動DMツール 実行ランチャー (run_launcher.py / run_launcher.pyw)
・コマンドプロンプト(cmd)の黒い画面を出さずにGUIアプリ画面だけを直接起動
・万一のエラー時もWindowsダイアログで分かりやすく通知
"""
import sys
import os
import subprocess
import traceback

base_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(base_dir)

def show_gui_error(title, message):
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(title, message)
        r.destroy()
    except Exception:
        pass

# 1. ZIP未解凍チェック
acc_info = os.path.join(base_dir, "アカウント情報.xlsx")
acc_name = os.path.join(base_dir, "アカウント名.xlsx")
if not os.path.exists(acc_info) and not os.path.exists(acc_name):
    msg = (
        "【警告】ZIPファイルが「すべて展開（解凍）」されていません！\n\n"
        "ダウンロードしたZIPファイルを右クリックし、\n"
        "「すべて展開」をクリックして展開されたフォルダ内で実行してください。"
    )
    show_gui_error("ZIP未解凍エラー", msg)
    if sys.stdout and hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
        print("=" * 65)
        print("⚠️ " + msg)
        print("=" * 65)
        try:
            input("\nEnterキーを押すと終了します...")
        except Exception:
            pass
    sys.exit(1)

# 2. logフォルダ & dbフォルダ 自動準備
os.makedirs(os.path.join(base_dir, "logフォルダ"), exist_ok=True)
os.makedirs(os.path.join(base_dir, "dbフォルダ"), exist_ok=True)

# 3. 必須ライブラリ確認 & 自動インストール
missing = []
try:
    import openpyxl
except ImportError:
    missing.append("openpyxl")

try:
    import playwright
except ImportError:
    missing.append("playwright")

if missing:
    try:
        req_path = os.path.join(base_dir, "requirements.txt")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        if os.path.exists(req_path):
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_path], check=True, creationflags=flags)
        else:
            subprocess.run([sys.executable, "-m", "pip", "install", "openpyxl", "playwright"], check=True, creationflags=flags)
        subprocess.run([sys.executable, "-m", "playwright", "install", "--force", "chromium"], check=True, creationflags=flags)
    except Exception as e:
        err_msg = (
            f"必須ライブラリ ({', '.join(missing)}) の自動セットアップに失敗しました:\n\n{e}\n\n"
            "インターネット接続を確認し、「00_初回環境構築_必要な場合.bat」を実行してください。"
        )
        show_gui_error("ライブラリ自動セットアップ失敗", err_msg)
        sys.exit(1)

# 4. GUIアプリケーション起動
try:
    import gui_launcher
    root = gui_launcher.tk.Tk()
    app = gui_launcher.AppLauncherGUI(root)
    root.mainloop()
    try:
        app.on_window_close()
    except Exception:
        pass
    os._exit(0)
except Exception as e:
    err_trace = traceback.format_exc()
    try:
        with open("launch_error.log", "w", encoding="utf-8") as f:
            f.write(err_trace)
    except Exception:
        pass
    show_gui_error(
        "アプリケーション起動エラー",
        f"アプリケーション起動中にエラーが発生しました:\n\n{e}\n\n※ 詳細は launch_error.log または「【エラー診断】起動テスト_画面が消える時はこれ.bat」で確認できます。"
    )
    if sys.stdout and hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
        print("=" * 65)
        print("❌ アプリケーション起動中にエラーが発生しました:")
        print("=" * 65)
        traceback.print_exc()
        try:
            input("\nEnterキーを押すと終了します...")
        except Exception:
            pass
    sys.exit(1)
