# -*- coding: utf-8 -*-
"""
Chrome カスタムプロファイル起動ツール (chrome_launcher)
- Playwright や自動化フラグを一切使わず、PCにインストール済みの正規 Google Chrome (chrome.exe) を
  アカウントごとの独立フォルダ (browser_profiles/<プロファイル名>) で直接起動します。
- 初回のみ手動で Chrome の「ログイン / ログアウト状態を保持」および X (Twitter) へログインし、
  ブラウザを閉じるだけで Cookie・IndexedDB・端末認証情報が永続保存されます。
- 2回目以降の自動実行 (main.py / test_single.py) では、このプロファイルをそのまま引き継ぐため
  再ログインや「問題が発生しました。やりなおしてください。」エラーが発生しません。
"""

import os
import sys
import time
import re
import shutil
import subprocess
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
        m = re.match(r'^(?:https?://)?(?:www\.)?(?:x|twitter)\.com/+@?', s, re.IGNORECASE)
        if m:
            s = s[m.end():].strip().lstrip("@").strip()
            continue
        if s.startswith("@"):
            s = s.lstrip("@").strip()
            continue
        if "x.com/" in s.lower() or "twitter.com/" in s.lower():
            parts = re.split(r'(?:https?://)?(?:www\.)?(?:x|twitter)\.com/+@?', s, flags=re.IGNORECASE)
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
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _find_registry_browser(exe_name: str) -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg
        subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for flags in (winreg.KEY_READ, winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0), winreg.KEY_READ | getattr(winreg, "KEY_WOW64_32KEY", 0)):
                try:
                    with winreg.OpenKey(hive, subkey, 0, flags) as key:
                        val, _ = winreg.QueryValueEx(key, None)
                        if val and os.path.isfile(str(val).strip('"')):
                            return str(val).strip('"')
                except OSError:
                    continue
    except Exception:
        pass
    return ""


def find_chrome_executable():
    """
    Playwright同梱Chromiumではなく、PCにインストール済みの正規 Google Chrome を最優先で探索する。
    戻り値: (executable_path, browser_label)
    """
    candidates = []

    if sys.platform == "win32":
        reg_chrome = _find_registry_browser("chrome.exe")
        if reg_chrome:
            candidates.append((reg_chrome, "Google Chrome"))

        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        ]
        for p in chrome_paths:
            if p and os.path.isfile(p):
                candidates.append((p, "Google Chrome"))

    for cmd in ("chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(cmd)
        if found and os.path.isfile(found):
            candidates.append((found, "Google Chrome"))

    if sys.platform == "win32":
        reg_edge = _find_registry_browser("msedge.exe")
        if reg_edge:
            candidates.append((reg_edge, "Microsoft Edge (Chrome代替)"))

        edge_paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        ]
        for p in edge_paths:
            if p and os.path.isfile(p):
                candidates.append((p, "Microsoft Edge (Chrome代替)"))

    edge_which = shutil.which("msedge")
    if edge_which and os.path.isfile(edge_which):
        candidates.append((edge_which, "Microsoft Edge (Chrome代替)"))

    for path, label in candidates:
        if path and os.path.isfile(path):
            return path, label

    return None, ""


def _cleanup_profile_before_launch(profile_dir: str):
    """過去のPlaywrightロックや古い storage_state.json が正規ログインを邪魔しないよう事前整理"""
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
        lp = os.path.join(profile_dir, lock_name)
        if os.path.exists(lp) or os.path.islink(lp):
            try:
                os.remove(lp)
            except Exception:
                pass

    state_file = os.path.join(profile_dir, "storage_state.json")
    if os.path.exists(state_file):
        try:
            os.remove(state_file)
        except Exception:
            pass


def _has_saved_cookies(profile_dir: str) -> bool:
    cookie_candidates = [
        os.path.join(profile_dir, "Default", "Network", "Cookies"),
        os.path.join(profile_dir, "Default", "Cookies"),
    ]
    return any(os.path.isfile(p) and os.path.getsize(p) > 0 for p in cookie_candidates)


def launch_chrome_profile(profile_name: str):
    base_dir = get_base_dir()
    clean_name = sanitize_profile_name(profile_name)
    profile_dir = os.path.join(base_dir, "browser_profiles", clean_name)
    os.makedirs(profile_dir, exist_ok=True)

    _cleanup_profile_before_launch(profile_dir)

    print(f"\n📁 プロファイル保存フォルダ: {profile_dir}")
    chrome_exe, browser_label = find_chrome_executable()

    if not chrome_exe:
        print("\n❌ Google Chrome (chrome.exe) が見つかりませんでした。")
        print("   正規ブラウザで手動ログインを行うため、Google Chrome をインストールしてください。")
        print("   URL: https://www.google.com/chrome/")
        return

    print(f"🌐 正規ブラウザ [{browser_label}] をカスタムプロファイルで起動します...")
    print(f"   実行パス: {chrome_exe}")

    cmd = [
        chrome_exe,
        f"--user-data-dir={profile_dir}",
    ]

    try:
        start_time = time.time()
        proc = subprocess.Popen(cmd, cwd=base_dir)

        print("\n" + "=" * 64)
        print(f"  ✨ [{browser_label}] がプロファイル [{clean_name}] で起動しました！")
        print("=" * 64)
        print("  【初回登録の手順】")
        print("  1. 「Chrome にログイン」画面が出たら「ログアウト状態を保持」（またはログイン）を選択")
        print("  2. 起動した Chrome で、ご自身で検索等から X (Twitter) へアクセスし、手動でログイン")
        print("  3. X のホーム画面 (タイムライン) が表示されることを確認")
        print("  4. 確認できたら、ブラウザ右上の【✕ ボタン】で Chrome を完全に閉じてください")
        print("")
        print("  ※ 閉じるだけで認証情報・Cookie が自動で保存されます。")
        print("  ※ 2回目以降の自動ツール実行時は、再ログイン不要でこのまま自動稼働します！")
        print("=" * 64)
        print("\n⏳ Chrome の終了を待機しています... (ログイン完了後に Chrome を閉じてください)")

        proc.wait()
        elapsed = time.time() - start_time

        if elapsed < 3.0:
            print("\nℹ️ 別の Chrome ウィンドウが開いている場合は、X へのログイン完了後にその Chrome を閉じてから")
            try:
                input("   [Enter キー] を押して保存完了を確認してください...")
            except Exception:
                pass

        for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            lp = os.path.join(profile_dir, lock_name)
            if os.path.exists(lp) or os.path.islink(lp):
                try:
                    os.remove(lp)
                except Exception:
                    pass

        print("\n" + "=" * 64)
        if _has_saved_cookies(profile_dir):
            print(f"🎉 プロファイル [{clean_name}] のログイン状態・Cookie 保存を確認しました！")
        else:
            print(f"✅ プロファイル [{clean_name}] のブラウザを終了しました。")
            print("   ※ もしXにログインする前に閉じた場合は、もう一度実行してXにログインしてください。")
        print(f"💡 「アカウント情報.xlsx」の アカウントID に「{clean_name}」と記載しておくと、")
        print("   2回目以降の自動実行時もこのプロファイルをそのまま使い、再ログイン不要で動作します！")
        print("=" * 64)

    except Exception as e:
        print(f"\n⚠️ Chrome の起動中にエラーが発生しました: {e}")
        traceback.print_exc()


def main():
    while True:
        print("=========================================")
        print("Chrome カスタムプロファイル起動ツール")
        print("=========================================")
        print()

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
            print("\nプロファイル名が入力されませんでした。もう一度入力してください（終了する場合は 'q'）。")
            print()
            continue

        if profile_name.lower() in ("q", "exit", "quit"):
            print("終了します。")
            break

        launch_chrome_profile(profile_name)

        print("\n別のアカウント / プロファイルも続けて作成・起動しますか？")
        try:
            ans = input("続ける場合は Enter キー（終了する場合は 'q' を入力）: ").strip()
            if ans.lower() in ("q", "exit", "quit"):
                break
        except Exception:
            break
        print()


if __name__ == "__main__":
    main()
