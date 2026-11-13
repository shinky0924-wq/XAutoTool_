"""
【安全確認用】単体テスト・動作確認スクリプト (test_single.py)
いきなり大量送信せず、1アカウント・1ターゲットで以下を個別確認できます:
1. auth_token Cookieログインが通るか
2. 単一タブで開いているか
3. ターゲットのページへアクセスし、DMボタンの有無（DM拒否・鍵垢判定）が正しく機能するか
4. ベジェ曲線マウス移動と人間らしいタイピングの動作
5. アカウント名.db への登録確認
"""

import os
import sys
import re
import time

import functools
print = functools.partial(print, flush=True)

# Windows CP932 / Emoji UnicodeEncodeError 対策
if sys.platform == "win32":
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from browser_controller import XBrowserController
from db_manager import (
    DatabaseManager,
    get_db_dir,
    get_account_db_path,
    get_log_dir,
    get_account_log_path,
    AccountLogger,
    sanitize_filename
)
from config_manager import ConfigManager

def normalize_screen_name(val: str) -> str:
    if not val:
        return ""
    s = str(val).strip().lstrip("@").strip()
    while True:
        m = re.match(r'^(?:https?://)?(?:www.)?(?:x|twitter).com/+@?', s, re.IGNORECASE)
        if m:
            s = s[m.end():].strip().lstrip("@").strip()
            continue
        if s.startswith("@"):
            s = s.lstrip("@").strip()
            continue
        if "x.com/" in s.lower() or "twitter.com/" in s.lower():
            parts = re.split(r'(?:https?://)?(?:www.)?(?:x|twitter).com/+@?', s, flags=re.IGNORECASE)
            s = parts[-1].strip().lstrip("@").strip()
            continue
        break
    s = s.split("?")[0].split("#")[0].rstrip("/")
    if "/" in s:
        s = s.split("/")[0]
    return s.lstrip("@").strip()

def sanitize_filename(name: str) -> str:
    """Windowsで使用不可能な記号を安全なアンダースコアに置換"""
    try:
        clean = normalize_screen_name(name)
        if not clean:
            clean = re.sub(r'[\/*?:"<>|]', '_', str(name or "").strip())
        if not clean:
            clean = "unknown_account"
        return clean
    except Exception:
        return "unknown_account"

def main():
    print("=" * 60)
    print("  X 自動DMツール 【単体テスト・安全動作確認モード】")
    print("=" * 60)

    base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    account_config_path = os.path.join(base_dir, "アカウント情報.xlsx")
    target_list_path = os.path.join(base_dir, "アカウント名.xlsx")
    if not os.path.exists(target_list_path):
        target_list_path = os.path.join(base_dir, "アカウント名情報.xlsx")
    db_path = os.path.join(base_dir, "アカウント名.db")

    config_mgr = ConfigManager(account_config_path, target_list_path)
    db_mgr = DatabaseManager(db_path)

    # 1. アカウント設定確認
    accounts = config_mgr.load_accounts()
    if not accounts:
        print("❌ アカウント情報.xlsx からアカウントが取得できませんでした。")
        return

    first_acc = accounts[0]
    raw_account_id = str(first_acc.get("アカウントID", "") or first_acc.get("アカウント名", "") or first_acc.get("ユーザー名", "")).strip()
    account_id = normalize_screen_name(raw_account_id) or "test_account"
    acc_target_path = config_mgr.get_target_path_for_account(account_id)

    # テスト対象アカウントの専用ログ & 専用DB
    acc_log_path = get_account_log_path(base_dir, account_id)
    acc_db_path = get_account_db_path(base_dir, account_id)
    acc_logger = AccountLogger(account_id, base_dir)
    db_mgr = DatabaseManager(acc_db_path)

    print(f"📁 アカウント専用ログ: {acc_log_path}")
    print(f"💾 アカウント専用DB: {acc_db_path}")
    print(f"📂 ターゲット設定ファイル: {os.path.basename(acc_target_path)}")

    auth_token = ""
    for k, v in first_acc.items():
        if k and v is not None and str(v).strip():
            k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
            if any(m in k_clean for m in ["auth_token", "authtoken", "セッションid", "session_id", "token", "クッキー", "cookie"]):
                auth_token = str(v).strip()
                break

    password = ""
    for k, v in first_acc.items():
        if k and v is not None and str(v).strip():
            k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
            if any(m in k_clean for m in ["アカウントpw", "アカウントパスワード", "パスワード", "password", "pass", "pw", "暗証番号"]):
                s = str(v).strip()
                if s.endswith(".0") and s[:-2].isdigit():
                    password = s[:-2]
                else:
                    password = s
                break

    acc_settings = config_mgr.load_account_settings(account_id, target_path_override=acc_target_path, fallback_acc=first_acc)
    dm_passcode = str(acc_settings.get("dm_passcode", "") or "").strip()
    if not dm_passcode:
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
            dm_passcode = saved_passes.get(account_id, "") or saved_passes.get(normalize_screen_name(raw_account_id), "")
        except Exception:
            pass

    print(f"\n[1] テスト対象アカウント: @{account_id}")
    print(f"    auth_token設定: {'あり (' + auth_token[:10] + '...)' if auth_token else 'なし (セッションID未設定)'}")
    print(f"    パスワード設定: {'あり (ID/PW自動フォームログイン有効)' if password else '未設定 (手動ログイン待機)'}")
    print(f"    パスコード設定: {'あり (暗号化DM/2FA自動入力有効)' if dm_passcode else '未設定'}")

    # 2. テスト送信先の入力 (Excelからのターゲット取得、または入力)
    default_target = ""
    try:
        targets = config_mgr.load_targets(target_path_override=acc_target_path)
        if targets:
            for t in targets:
                t_name = normalize_screen_name(str(t.get("アカウント名", "") or t.get("ターゲットURL", "") or t.get("ユーザー名", "") or ""))
                if t_name and t_name.lower() != "elonmusk":
                    default_target = t_name
                    break
    except Exception:
        pass

    if not default_target:
        default_target = "sample_test_user"

    target_input = ""
    try:
        if sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
            target_input = input(f"\n[2] テスト確認したいターゲットのスクリーン名またはポストURLを入力 (未入力なら @{default_target}): ").strip()
    except (EOFError, Exception):
        target_input = ""
    target_name = normalize_screen_name(target_input if target_input else default_target)
    target_tweet_url = target_input if "/status/" in target_input else ""

    # 3. テストメッセージ
    test_msg = f"{target_name}さん、初めまして！テスト送信です。失礼いたします。"
    print(f"\n[3] テスト送信文面: 「{test_msg}」")

    # 4. モード選択
    print("\n[4] 実行モード:")
    print("    1: DOM判定テストのみ (DMボタンの検知とDM拒否判定のみ確認。送信はしない)")
    print("    2: DM送信テスト (人間らしいタイピング＋ベジェ曲線＋送信実行)")
    print("    3: ❤️ いいねテスト (取得ポストまたは最新ポストへのいいね実行)")
    print("    4: 👤 フォローテスト (プロファイルへのアクセスとフォロー実行)")
    print("    5: 🚀 フルテスト (フォロー ＋ いいね ＋ DM送信の一連の流れ)")
    choice = "1"
    try:
        if sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
            choice = input("    番号を選択 (1〜5) [初期値: 1]: ").strip()
    except (EOFError, Exception):
        choice = "1"

    # ブラウザ起動 (提案2: 画像・動画ブロックで超軽量起動)
    print("\n🚀 ブラウザを起動します (headless=False: 画面表示 / ⚡ 画像・動画メディア遮断で高速・省メモリ)...")
    profile_dir = os.path.join(base_dir, "browser_profiles", sanitize_filename(account_id))
    ctrl = XBrowserController(headless=False, user_data_dir=profile_dir, base_dir=base_dir, block_media=True)
    
    try:
        ctrl.start()
        
        # ログインテスト
        print("🔑 ログインを試行中...")
        logged_in = ctrl.login(account_id=account_id, auth_token=auth_token, password=password, passcode=dm_passcode)
        if not logged_in:
            print("❌ ログインに失敗しました。auth_token やパスワードを確認してください。")
            return
        
        print("✅ ログイン成功！")
        time.sleep(2)

        # 一時停止チェック（GUIの一時停止ボタン対応・ウィンドウ保持）
        try:
            from main import check_pause_state
            check_pause_state("安全テストログイン直後", ctrl)
        except Exception:
            pass

        # ターゲットページ移動
        if target_tweet_url:
            print(f"\n🌐 取得ツイート {target_tweet_url} を対象にテストを実行します...")
        else:
            print(f"\n🌐 ターゲット @{target_name} のページへ移動します...")
        
        if choice == "3" or choice == "5":
            print(f"\n❤️ [いいねテスト実行中] @{target_name}")
            st, det = ctrl.like_recent_post(target_name, tweet_url=target_tweet_url)
            print(f"   ❤️ いいね結果: {st} ({det})")
            if st in ["SUCCESS", "ALREADY_LIKED"]:
                db_mgr.record_sent(target_name, account_id, "LIKE", st, det)
            time.sleep(2)

        if choice == "4" or choice == "5":
            print(f"\n👤 [フォローテスト実行中] @{target_name}")
            st, det = ctrl.follow_user(target_name)
            print(f"   👤 フォロー結果: {st} ({det})")
            if st in ["SUCCESS", "ALREADY_FOLLOWING"]:
                db_mgr.record_sent(target_name, account_id, "FOLLOW", st, det)
            time.sleep(2)

        if choice == "1":
            test_page = ctrl.ensure_page()
            ctrl.wait_for_profile_ready(target_name, timeout=20.0)
            
            print("   🔍 DMボタンを探索中（React描画待機）...")
            dm_button = None
            start_f = time.time()
            while time.time() - start_f < 14.0:
                for sel in [
                    'button[data-testid="sendDMFromProfile"]', 
                    '[data-testid="sendDMFromProfile"]', 
                    'button[aria-label*="メッセージを送信"]', 
                    'button[aria-label*="メッセージ"]', 
                    'button[aria-label*="Message"]'
                ]:
                    loc = test_page.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible():
                        dm_button = loc.first
                        break
                if dm_button:
                    break
                time.sleep(0.4)

            is_locked = test_page.locator('svg[data-testid="icon-lock"]').count() > 0
            
            print("\n--- 【判定結果】 ---")
            print(f"・鍵アカウントアイコン: {'検出 (非公開)' if is_locked else 'なし (公開)'}")
            if dm_button:
                print("・DMボタン: 検出！ (DM送信可能なアカウントです)")
                print("   ※ モード1のため送信ボタンは押さずに安全に終了します。")
            else:
                print("・DMボタン: 非表示 (相互フォロー外拒否 または 鍵アカウント)")
                print("   ⚡ 本番スクリプトではこの状態で即座に「DM拒否」として次へスキップします！")
        elif choice == "2" or choice == "5":
            # 一時停止チェック（送信前にブラウザ画面で文面やターゲットを目視確認可能）
            try:
                from main import check_pause_state
                check_pause_state("テストDM送信直前", ctrl)
            except Exception:
                pass
            # 実際のDM送信
            status, detail = ctrl.send_direct_message(target_name, test_msg, passcode=dm_passcode)
            print(f"\n✉️ DM送信結果: {status} ({detail})")
            if status == "SUCCESS":
                db_mgr.record_sent(target_name, account_id, "DM", "SUCCESS", test_msg[:30])
                print(f"✅ 専用DB ({os.path.basename(acc_db_path)}) への登録も完了しました！")

    except Exception as e:
        print(f"❌ エラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\nブラウザを終了します...")
        ctrl.close()
        print("テストが終了しました。")

if __name__ == "__main__":
    main()
    try:
        if sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
            print("\n" + "=" * 60)
            input("Enterキーを押すと終了します...")
    except (EOFError, KeyboardInterrupt, Exception):
        pass
