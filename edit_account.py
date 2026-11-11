# -*- coding: utf-8 -*-
"""
X(Twitter) アカウント編集・設定確認モード (ウィンドウ保持)
ブラウザを起動してログインし、ユーザーがアカウント情報を編集・確認できるようにウィンドウを開いたまま待機します。
"""

import os
import sys
import time

if sys.platform == "win32":
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import re
from config_manager import ConfigManager
from db_manager import sanitize_filename
from browser_controller import XBrowserController

def sanitize_filename(name: str) -> str:
    try:
        clean = re.sub(r'[\/*?:"<>|]', '_', str(name or "").strip())
        return clean or "unknown_account"
    except Exception:
        return "unknown_account"

def main():
    print("=" * 60)
    print("  🌐 X(Twitter) アカウント編集・設定確認モード (ウィンドウ保持)")
    print("=" * 60)

    base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    acc_path = os.path.join(base_dir, "アカウント情報.xlsx")
    target_path = os.path.join(base_dir, "アカウント名.xlsx")
    config_mgr = ConfigManager(acc_path, target_path)

    accounts = config_mgr.load_accounts()
    if not accounts:
        print("❌ アカウント情報.xlsx からアカウントが読み込めませんでした。")
        return

    first_acc = accounts[0]
    account_id = first_acc.get("アカウントID", "").strip()
    auth_token = first_acc.get("auth_token", "").strip()
    password = first_acc.get("パスワード", "").strip()
    dm_raw = ""
    for k, v in first_acc.items():
        if k and v is not None and str(v).strip():
            k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
            if any(m in k_clean for m in ["dmパスコード", "パスコード", "暗号化パスコード", "暗号化キー", "pin", "2fa", "passcode"]):
                dm_raw = str(v).strip()
                break
    dm_passcode = dm_raw[:-2] if dm_raw.endswith(".0") and dm_raw[:-2].isdigit() else dm_raw
    if not dm_passcode:
        try:
            from db_manager import get_saved_passcodes
            saved_passes = get_saved_passcodes(base_dir)
            dm_passcode = saved_passes.get(account_id, "") or saved_passes.get(normalize_screen_name(account_id), "")
        except Exception:
            pass

    print(f"🚀 アカウント: @{account_id} でブラウザを起動します (画面表示)...")
    profile_dir = os.path.join(base_dir, "browser_profiles", sanitize_filename(account_id))
    browser_ctrl = XBrowserController(headless=False, user_data_dir=profile_dir, base_dir=base_dir)
    try:
        browser_ctrl.start()
        success = browser_ctrl.login(account_id=account_id, auth_token=auth_token, password=password, passcode=dm_passcode)
        if success:
            print(f"✅ @{account_id} のログインに成功しました！")
            page = browser_ctrl.ensure_page()
            try:
                page.goto(f"https://x.com/{account_id}", timeout=30000, wait_until="domcontentloaded")
            except Exception:
                pass
            print("")
            print("=" * 60)
            print("✨ 【アカウント編集待機中】ブラウザウィンドウを開いたまま保持しています。")
            print("   ・プロフィールの変更（アイコン、ヘッダー、名前、自己紹介文）")
            print("   ・アカウント設定の確認（パスワード、2段階認証、通知設定など）")
            print("   上記の編集・確認を自由に行ってください。")
            print("   ※ 終了したい場合は、ブラウザ画面の「✕」ボタンで閉じるか、GUIで「強制停止」を押してください。")
            print("=" * 60)
            
            while True:
                try:
                    if not browser_ctrl.page or browser_ctrl.page.is_closed():
                        print("ブラウザウィンドウが閉じられました。")
                        break
                    time.sleep(1)
                except Exception:
                    break
        else:
            print("❌ ログインに失敗しました。認証情報をご確認ください。")
            time.sleep(5)
    except Exception as e:
        print(f"エラー: {e}")
    finally:
        browser_ctrl.close()
        print("アカウント編集セッションを終了しました。")

if __name__ == "__main__":
    main()
