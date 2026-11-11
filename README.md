# X (Twitter) 自動DM・リプライ・送信ツール Pro

Playwright (Python 3.12) を使用した、人間らしい操作＆凍結（BAN）対策済みの自動DM送信ツールです。

---

## 📁 フォルダ構成 (このPCでの開発・管理用)

```
C:\Users\PC_User\Desktop\x_auto_dm_tool\
├── 00_初回環境構築.bat        # 初回環境構築 (Python診断 & pip & Playwright Chromium)
├── アプリ起動_GUI.bat         # デスクトップGUIアプリ起動 (ボタン操作画面)
├── 他PC配布用パッケージ作成.bat # ★ 他PC用EXE化＆ブラウザ同梱ZIPをワンクリック自動生成
├── build_dist_package.py     # 他PC配布用パッケージ作成Pythonスクリプト
├── gui_launcher.pyw          # GUIアプリ画面本体 (Tkinter)
├── main.py                   # メイン処理エントリポイント (無限ループ巡回)
├── test_single.py            # 単体テストスクリプト
├── browser_controller.py     # Playwright操作 (同梱ブラウザ自動検知)
├── config_manager.py         # Excel読み込み (PermissionError自動対策付き)
├── db_manager.py             # SQLite3 (アカウント名.db) 重複送信防止
├── アカウント情報.xlsx        # アカウント設定・認証token・実行パラメータ
├── アカウント名.xlsx          # ターゲットURLリスト・検索キーワード・文面
├── requirements.txt          # 依存ライブラリ一覧
└── x_auto_dm.spec            # PyInstaller 配布ビルド設定
```

---

## 📦 他PCへ配布して「解凍するだけ」で動かす手順

このPC（Python環境があるPC）で一度だけコンパイルを行い、他PCへ配布します。

### ステップ1: このPCでパッケージ作成を実行
`他PC配布用パッケージ作成.bat` をダブルクリック（またはGUIの「📦 他PC配布用パッケージ作成」ボタンをクリック）します。
自動的に以下が実行されます：
1. PythonコードをEXE化（PyInstaller）
2. Playwright Chromiumブラウザ本体を丸ごと同梱
3. 超シンプルなフォルダ構成に自動整理
4. `【他PC用】x_auto_dm_tool_完全版.zip` を自動生成！

### ステップ2: 他のPCへ送る
作成された `【他PC用】x_auto_dm_tool_完全版.zip` をUSBメモリやGoogle Drive等で別のPCへコピーします。

### ステップ3: 他のPCで実行（解凍するだけ！）
1. ZIPファイルを解凍（展開）します。
2. フォルダ内は以下の**超シンプル構成**（たったの4ファイル）になっています：
   ```
   【他PC配布用】x_auto_dm_tool/
   ├── 🚀 ツール起動.bat       ← これをダブルクリックするだけ！
   ├── x_auto_dm_gui.exe      ← アプリ本体 (直接起動も可能)
   ├── アカウント情報.xlsx    ← アカウント設定
   ├── アカウント名.xlsx      ← ターゲット・メッセージ設定
   ├── README_他PCでの使い方.txt
   └── _internal\             ← Python・ブラウザ・全依存ファイル同梱
   ```
3. `🚀 ツール起動.bat` をダブルクリックするだけで即座に起動します！
   ※ 他のPC側でのPythonインストール、pipコマンド、ブラウザダウンロードは【一切不要】です！

---

## ⚠️ トラブルシューティング（よくある質問）

### Q1. 「一瞬黒くなって消える」「'python' は、内部コマンドまたは外部コマンド...として認識されていません」
**原因**: WindowsにPythonがまだ入っていないか、インストール時に「Add python.exe to PATH」にチェックが入っていません。

**解決策 (次のどれか1つで解決します)**:
1. **最も簡単 (おすすめ)**:
   Windowsの「Microsoft Store」アプリを開き、「Python 3.12」と検索して「入手」をクリックするだけです（管理者権限不要、PATH自動設定）。
2. **コマンドプロンプトで1行実行**:
   ```cmd
   winget install Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
   ```
3. **公式サイトから導入**:
   https://www.python.org/downloads/ からダウンロードし、
   インストール起動時の最下部にある **「Add python.exe to PATH」に必ずチェックを入れて** から「Install Now」を押してください。

インストール後、`00_初回環境構築.bat` をダブルクリックすると正常に完了します。

---

## 🚀 アプリの起動方法

1. **このPCでGUIアプリを起動したい場合**:
   - `アプリ起動_GUI.bat` をダブルクリックします。
   - Excelファイルを開くボタンや「自動収集＆DM送信を実行」「1件のみ安全テスト」「他PC配布用パッケージ作成」が並んだデスクトップアプリ画面が起動します。

2. **他PC用の配布パッケージを作成したい場合**:
   - `他PC配布用パッケージ作成.bat` をダブルクリックします。
   - `【他PC用】x_auto_dm_tool_完全版.zip` が自動作成されます。
