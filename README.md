# Token Checker for Claude Code / Codex

Claude Code と Codex の**残り使用量をタスクトレイから確認できる** Windows 常駐アプリです。

「あとどれくらい使えるのか」を調べるためにブラウザを開き直す手間をなくすことだけを目的にしています。

- 対象 OS: Windows 10 / Windows 11
- 常駐先: タスクトレイ
- 更新間隔: 既定 300 秒（設定で変更可）
- 残量が閾値を切ると通知（既定 20% / 10%）

---

## このアプリが何を読み、どこへ送るのか

このツールは**あなたの認証情報を読みます**。有料で配布しているソフトウェアが認証ファイルに触れる以上、
中身を隠したままでは信用できないと考えているため、ソースを公開しています。

### 読み取るファイル

| ファイル | 用途 |
| --- | --- |
| `~/.claude/.credentials.json` | Claude のアクセストークン取得 |
| `~/.codex/auth.json` | Codex のアクセストークン取得 |

`APPDATA` / `LOCALAPPDATA` 配下の同等ファイル、および `CODEX_HOME` も候補として探索します。

### 送信先

以下の **2 つの公式エンドポイントのみ**です。ほかのホストへは一切通信しません。

| 送信先 | 目的 |
| --- | --- |
| `https://api.anthropic.com/api/oauth/usage` | Claude の使用量取得 |
| `https://chatgpt.com/backend-api/wham/usage` | Codex の使用量取得 |

いずれも、取得したトークンを `Authorization: Bearer` ヘッダーに付けて使用量を問い合わせるだけです。
該当箇所は [`src/token_checker.py`](src/token_checker.py) の `fetch_claude_usage()` と `fetch_codex_usage()` です。

### トークンの取り扱い

- **トークンの値をログに出力しません。** ログに記録するのは JSON のキー名のみです
  （例: `top-keys=['claudeAiOauth']`）。値は一切書き出しません
- **トークンをディスクへ保存しません。** 取得したトークンはメモリ上でのみ使用します
- **収集・集計・第三者送信を行いません。** テレメトリはありません

### ログに書かれるもの

実行ファイルと同じフォルダーに `token_checker.log` を作成します。
パスは `_mask_path()` によりホームディレクトリを `~` に置換してから記録します。

```
[2026-08-18 09:07:34] [Claude] ファイル読み込み: ~/.claude/.credentials.json  top-keys=['claudeAiOauth']
[2026-08-18 09:07:34] [Claude] トークン取得成功 (claudeAiOauth.accessToken)
[2026-08-18 09:07:35] [Claude API] status=200
```

不具合の問い合わせでこのログを送っていただく場合がありますが、
**Windows ユーザー名やトークンは含まれません。**

---

## 自分でビルドする

ビルド済みの実行ファイルは BOOTH で配布していますが、
このリポジトリから自分でビルドすることもできます。内容は同じです。

### 必要なもの

- Python 3.10 以上
- `pip install requests pystray pillow`

### 手順

```powershell
cd src
.\build.bat
```

`src\TokenChecker.exe` が生成されます。PyInstaller の `--onefile` で単一ファイルにまとめています。

### 直接実行する

ビルドせずにそのまま動かすこともできます。

```powershell
cd src
python token_checker.py
```

---

## 使い方

詳細は [docs/使い方ガイド.md](docs/使い方ガイド.md) を参照してください。

---

## 既知の制限

- **Windows 専用**です。macOS / Linux では動作しません
- 認証ファイルの形式が変わった場合、トークンを取得できなくなることがあります。
  その場合はログに `[Claude] 既知キーが見つかりません` と記録されます
- 使用量 API は公式に文書化されたものではないため、提供側の仕様変更で取得できなくなる可能性があります
- 未署名の実行ファイルのため、SmartScreen の警告が表示されることがあります

---

## ライセンス

検討中です。確定するまでは、本リポジトリのコードは閲覧・検証目的で公開されているものとしてお取り扱いください。
