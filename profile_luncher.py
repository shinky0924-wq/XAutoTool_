# -*- coding: utf-8 -*-
"""
Chrome カスタムプロファイル起動ツール (profile_launcher / profile_luncher)
- 指定したプロファイル名 (例: @user1, acc01) ごとに独立した browser_profiles/<profile_name> を作成・起動
- ブラウザ上で X (Twitter) に ID/PW でログインし、Cookie・IndexedDB・端末トークン・暗号化キーを永続保存
- 次回以降の自動送信ツール実行時も同一プロファイルを引き継ぐため、強制ログアウトや2段階認証の再要求を完全防止
"""

import os
import sys
import time
import re
import traceback

# Windows CP932 / Emoji UnicodeEncodeError 対策
if sys.platform == "win32":
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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

def sanitize_profile_name(name: str) -> str:
    cleaned = normalize_screen_name(name)
    if not cleaned:
        cleaned = re.sub(r'[\\/*?:"<>|]', '_', str(name).strip())
    return cleaned or "default_profile"

def get_base_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def launch_profile(profile_name: str):
    base_dir = get_base_dir()
    clean_name = sanitize_profile_name(profile_name)
    profile_dir = os.path.join(base_dir, "browser_profiles", clean_name)
    os.makedirs(profile_dir, exist_ok=True)

    print(f"\n📁 プロファイル保存フォルダ: {profile_dir}")
    print(f"🌐 プロファイル [{clean_name}] のブラウザを起動しています... しばらくお待ちください。")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("\n❌ Playwright がインストールされていません。")
        print("   00_初回環境構築_必要な場合.bat を実行してライブラリを導入してください。")
        try:
            input("\n[Enterキーを押して戻る]")
        except Exception:
            pass
        return

    try:
        with sync_playwright() as p:
            args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--disable-notifications",
                "--lang=ja-JP",
            ]
            
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=False,
                args=args,
                viewport={"width": 1280, "height": 840},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                locale="ja-JP",
                timezone_id="Asia/Tokyo"
            )

            page = context.pages[0] if context.pages else context.new_page()

            try:
                page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                """)
            except Exception:
                pass

            print("\n" + "=" * 60)
            print("  ✨ ブラウザウィンドウが正常に立ち上がりました！")
            print("=" * 60)
            print("  👉 ブラウザ上で X (Twitter) に 【ID・パスワード】 でログインしてください。")
            print("  👉 2段階認証(2FA)や確認メールが届いた場合も、この画面でそのまま進めてください。")
            print("  👉 ログインが完了したら、タイムラインやDMをご確認いただけます。")
            print("  👉 終了する時は、【ブラウザ右上の ✕ ボタン】を押して閉じてください。")
            print("     (閉じるだけでプロファイル・Cookie・ログイン状態が安全に自動保存されます)")
            print("=" * 60)

            try:
                page.goto("https://x.com/home", timeout=45000)
            except Exception:
                try:
                    page.goto("https://x.com/i/flow/login", timeout=30000)
                except Exception:
                    pass

            print("\n⏳ ブラウザの終了を待機中... (ブラウザウィンドウを閉じると保存完了します)")
            
            try:
                while True:
                    if not context.pages or page.is_closed():
                        break
                    time.sleep(1.0)
            except Exception:
                pass

            try:
                context.close()
            except Exception:
                pass

            print(f"\n🎉 ブラウザが正常に終了し、プロファイル [{clean_name}] の保存が完了しました！")
            print(f"💡 「アカウント情報.xlsx」の アカウントID に '{clean_name}' と登録しておくと、")
            print(f"   今後の自動送信ツール実行時もこのプロファイルが自動適用され、")
            print(f"   強制ログアウトされることなくログイン状態が永続維持されます！")
            print("=" * 60)

    except Exception as e:
        print(f"\n⚠️ ブラウザ実行中にエラーが発生しました: {e}")
        traceback.print_exc()

def main():
    while True:
        print("============================================================")
        print("Chrome カスタムプロファイル起動ツール")
        print("============================================================")
        
        if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith("-"):
            profile_name = sys.argv[1].strip()
            sys.argv = [sys.argv[0]]
        else:
            try:
                profile_name = input("作成・使用するプロファイル名を入力してください: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n終了します。")
                break

        if not profile_name:
            print("プロファイル名が入力されませんでした。Enterキーでもう一度入力するか、'q'で終了します。")
            try:
                retry = input("再試行する場合はプロファイル名を入力 (終了は 'q'): ").strip()
                if retry.lower() in ['q', 'exit', 'quit']:
                    break
                if not retry:
                    continue
                profile_name = retry
            except Exception:
                break

        if profile_name.lower() in ['q', 'exit', 'quit']:
            print("終了します。")
            break

        launch_profile(profile_name)

        print("\n別のプロファイルを作成・起動しますか？")
        try:
            ans = input("Enterキーで次のプロファイル入力へ (終了は 'q'): ").strip()
            if ans.lower() in ['q', 'exit', 'quit']:
                break
        except Exception:
            break

if __name__ == "__main__":
    main()
