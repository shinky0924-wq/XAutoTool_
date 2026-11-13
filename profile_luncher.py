# -*- coding: utf-8 -*-
"""
Chrome カスタムプロファイル起動ツール (profile_launcher / profile_luncher)
正規 Google Chrome (chrome_launcher) を呼び出し、手動ログインによる永続プロファイルを作成します。
"""

from chrome_launcher import (
    normalize_screen_name,
    sanitize_profile_name,
    get_base_dir,
    launch_chrome_profile as launch_profile,
    main,
)

if __name__ == "__main__":
    main()
