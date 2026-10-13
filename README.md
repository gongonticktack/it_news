# IT NEWS — AI・ITニュースの日刊サイト

## 概要

RSSとGoogle NewsからAI・ITニュースを収集し、重要度や重複を考慮して日刊記事を作成するサイトです。GitHub Actionsで自動生成し、GitHub Pagesへ公開します。日付別の閲覧と日英表示の切替に対応しています。

[公開サイトを開く](https://gongonticktack.github.io/it_news/)

## 主な機能

- 新着ニュースを5件程度選定。
- 見出し・概要・重要性・情報源を表示。
- 配信履歴を使った重複抑制。
- 英語記事の日本語化と日英切替。
- 日付別アーカイブと過去分の再構成。

## 推奨要件

| 用途 | 環境 |
| --- | --- |
| 閲覧 | 現行ブラウザ |
| ローカル生成 | Python 3.11、インターネット接続 |
| 定期公開 | GitHub Actions、GitHub Pages |
| AI要約を利用 | OpenAI APIキー（任意） |

## 必要なライブラリ

`requirements.txt`から導入します。

| ライブラリ | 用途 |
| --- | --- |
| feedparser | RSS解析 |
| requests | HTTP取得 |
| beautifulsoup4 | HTML解析 |
| deep-translator | 翻訳 |
| openai | 任意のAI要約 |

## 使い方

### 閲覧

公開サイトから日付を選び、ニュースと情報源を確認します。画面の日英ボタンで表示言語を切り替えられます。

### 自分のリポジトリで自動配信

1. リポジトリを取得し、GitHub Actionsを有効にします。
2. Settings → Pagesで公開元をGitHub Actionsに設定します。
3. `config/sources.json`の情報源を確認・調整します。
4. AI要約を使用する場合だけ、ActionsのSecretsに`OPENAI_API_KEY`を登録します。
5. Actionsの「IT NEWS daily」→「Run workflow」で初回生成を実行します。

定期実行は06:17 JST、予備実行は06:47 JSTです。07:00までの公開を目指す設定ですが、GitHubの予約実行は遅れる場合があります。同日の記事が存在すると収集を省略し、アーカイブとサイトを再構築します。

### ローカル生成

ルートで実行します。

```bash
python -m venv .venv
# OSに応じて .venv を有効化
python -m pip install -r requirements.txt
python scripts/daily_news.py
python -m http.server 8000 --directory docs
```

`http://localhost:8000/`を開きます。既存記事だけ再描画する場合：

```bash
python -c "from scripts.daily_news import build_site; build_site()"
```

## 設定

| 設定 | 内容 |
| --- | --- |
| `config/sources.json` | 取得するRSS・検索元 |
| `config/backfill.json` | 過去分再構成の設定 |
| `OPENAI_API_KEY` | AI要約を使用する場合のキー |
| `OPENAI_MODEL` | 要約モデル。コード既定値は`gpt-4.1-mini` |
| `REPORT_DATE` | 生成対象日。未指定時は日本時間の当日 |

APIキーを指定しなくても動作する構成です。AI要約の品質と外部翻訳サービスの可用性は利用先に依存します。

## フォルダ構成

| 相対パス | 役割 |
| --- | --- |
| `scripts/daily_news.py` | 収集、選定、翻訳、サイト生成 |
| `scripts/backfill.py` | 過去分再構成 |
| `config/` | 情報源・再構成設定 |
| `reports/` | 日付別JSON・記事の保存 |
| `docs/` | 公開HTML、日付別記事、共通CSS |
| `data/` | 配信履歴 |
| `tests/` | ニュース処理・表示のテスト |
| `.github/workflows/daily.yml` | 定期実行とPages公開 |
| `requirements.txt` | Python依存関係 |

## 仕組み

ニュースを取得し、公開日時・話題・履歴から候補を絞ります。記事を日本語化して日付別に保存し、閲覧用HTMLを作成します。Actionsが成果物をコミットし、Pagesへ公開します。

## 確認方法

```bash
python -m unittest discover -s tests -v
```

## トラブル対処

- 更新されない：Actionsの失敗ステップ、Pages設定、当日の`reports/`を確認してください。
- 記事取得・翻訳に失敗：情報源や翻訳先への通信を確認してください。
- 同じ日の収集をやり直したい：当日記事の存在判定により省略されるため、ワークフローの判定と保存済み記事を確認してください。

過去分の再構成は当時の配信内容そのものの復元ではありません。
