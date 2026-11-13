# X (Twitter) 自動DM・リプライ・送信ツール Pro

Playwright (Python 3.12) を使用した、人間らしい操作＆凍結（BAN）対策済みの自動DM送信ツールです。

---

## 📁 フォルダ構成 (このPCでの開発・管理用 / 重複ファイル整理済み)

```
C:\Users\PC_User\Desktop\x_auto_dm_tool\
├── 00_初回環境構築_必要な場合.bat                  # 初回環境構築 (pip & Playwright Chromium)
├── 01_このPCでツール起動.bat                       # デスクトップGUIアプリ起動
├── 02_他PC用ZIP作成_コンパイル.bat                 # ★ 他PC用EXE化＆ブラウザ同梱ZIPをワンクリック自動生成
├── chrome_launcher.bat                             # 正規Chrome手動ログインプロファイル作成
├── 【エラー診断】起動テスト_画面が消える時はこれ.bat
├── 【修復】ブラウザ起動エラー解消.bat
├── run_launcher.pyw                                # コンソールなしGUIランチャー
├── gui_launcher.py                                 # GUIアプリ画面本体 (Tkinter)
├── main.py                                         # メイン処理エントリポイント
├── ai_generator.py                                 # AI文章生成エンジン統合モジュール
├── test_single.py                                  # 単体テストスクリプト
├── edit_account.py                                 # アカウント編集用ブラウザ起動
├── chrome_launcher.py                              # 正規Chrome手動プロファイル起動
├── browser_controller.py                           # Playwright操作
├── config_manager.py                               # Excel読み込み
├── db_manager.py                                   # SQLite3 重複送信防止
├── build_dist_package.py                           # 他PC配布用パッケージ自動ビルド
├── diagnose.py                                     # 環境診断スクリプト
├── x_auto_dm.spec                                  # PyInstaller ビルド設定
├── requirements.txt                                # 依存ライブラリ一覧
├── アカウント情報.xlsx                             # アカウント設定・認証情報
└── アカウント名.xlsx                               # ターゲットURL・キーワード・文面
```

---

## 📦 Pythonが入っていない他PCへ配布して「解凍するだけ」で動かす手順

Pythonが入っているこのPCで**一度だけ**コンパイルを行い、生成されたフォルダまたはZIPを他PCへコピーします。

### ステップ1: PythonがあるPCでビルドを実行
`02_他PC用ZIP作成_コンパイル.bat` をダブルクリック（またはGUIの「📦 他PC配布用パッケージ作成」ボタンをクリック）します。
自動的に以下が実行されます：
1. PyInstallerで全機能統合EXE（`x_auto_dm_gui.exe`）を1つだけビルド（重複EXE・不要ファイル・`.py`生ソースは除外）
2. Playwright Chromiumブラウザ本体を `_internal/ms-playwright` に同梱（Firefox/WebKitは除外して軽量化）
3. `dist\【他PC用】x_auto_dm_tool\` フォルダと `【他PC用】x_auto_dm_tool_完全版.zip` を自動生成！

### ステップ2: Pythonが入っていないPCへコピー
作成された `【他PC用】x_auto_dm_tool_完全版.zip`（または `dist\【他PC用】x_auto_dm_tool` フォルダ）をUSBメモリやGoogle Drive等で別PCへコピーします。

### ステップ3: 他のPCで実行（Pythonインストール不要！）
1. ZIPファイルを右クリック ➜ **「すべて展開」** で解凍します。
2. フォルダ内は不要・重複ファイルなしの**最小構成**になっています：
   ```
   【他PC用】x_auto_dm_tool/
   ├── 🚀 ツール起動.bat                            ← これをダブルクリックするだけ！
   ├── 【エラー確認用】コマンドプロンプトで起動.bat  ← トラブル時のみ使用
   ├── x_auto_dm_gui.exe                            ← アプリ本体 (直接起動も可能)
   ├── アカウント情報.xlsx                          ← アカウント設定
   ├── アカウント名.xlsx                            ← ターゲット・メッセージ設定
   ├── README_他PCでの使い方.txt
   ├── logフォルダ\ / dbフォルダ\ / browser_profiles\
   └── _internal\                                   ← Python実行環境・Chromium・全依存ファイル同梱
   ```
3. `🚀 ツール起動.bat`（または `x_auto_dm_gui.exe`）をダブルクリックするだけで即座に起動します！
   ※ 他のPC側でのPythonインストール、pipコマンド、ブラウザダウンロードは【一切不要】です！
   ※ 「WindowsによってPCが保護されました」と出た場合は「詳細情報」➜「実行」をクリックしてください。

---

## ⚠️ トラブルシューティング

### Q1. 「一瞬黒くなって消える」「'python' は認識されていません」
- Pythonが入っているPCでセットアップする場合は、Microsoft Storeで「Python 3.12」を入手し、`00_初回環境構築_必要な場合.bat` を実行してください。
- Pythonを入れずに他PCで使いたい場合は、必ず **ビルド済みの `【他PC用】x_auto_dm_tool_完全版.zip`** を展開して `🚀 ツール起動.bat` から起動してください。
