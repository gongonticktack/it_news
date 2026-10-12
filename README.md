# IT NEWS — 毎朝7時のAI・ITニュース5選

🌐 **公開サイト（GitHub Pages）：[IT NEWSを開く](https://gongonticktack.github.io/it_news/)**

**株式日報とは独立**したニュースサイトです。GitHub Actionsで毎朝06:17 JSTに実行を予約し、06:47 JSTに予備実行します。7時までの公開を目指して、RSSから新着ニュースを選定して日付別のHTML/JSONに保存します。GitHub Pagesに公開します。

## セットアップ

1. リポジトリ `gongonticktack/it_news` の **Settings → Pages → Build and deployment → Source → GitHub Actions** を選択。
2. **Actions → IT NEWS daily → Run workflow** で手動実行し、初回の結果を確認。
3. 公開サイト：[IT NEWS](https://gongonticktack.github.io/it_news/)。
4. 任意: **Settings → Secrets and variables → Actions** に `OPENAI_API_KEY` を登録すると、取得済みニュースの日本語要約と重要性をOpenAI APIで生成。ChatGPT Plusとは別途API従量課金が発生します。キー未設定時も無料翻訳を試み、失敗した場合は原文と翻訳失敗の表示を残します。

## 配信内容と品質

- GitHub、Cloudflare、AWS、Googleなどの公式RSSとGoogle News RSSから候補を収集。
- 公開日時が確認でき、原則過去72時間以内のニュースを優先。最大7日まで補充。古い記事を新着扱いしない。
- 過去14日分のURLと類似見出しを除外。重大な続報を識別する専用の意味理解はAPI利用時でも保証されず、記事の誤検出・取りこぼしがあり得ます。
- 見出し、概要、なぜ重要か、情報源、公開日時、元記事リンクを掲載。
- AI要約はRSS由来のタイトル・概要のみを根拠とし、記事本文の精読や事実確認をしたものではありません。
- 5件未満しか新着がない場合は件数を減らして公開。新着が0件の場合は明記して公開。
- GitHub Actionsは混雑により遅れる場合があり、07:00までの公開は保証されません。予備実行は生成済みの日報を再利用し、未生成なら収集を再試行します。日付と生成物を検証し、失敗時はActionsにエラーを残します。
- PublicリポジトリとGitHub Pagesは**全世界に公開**されます。アクセス制限はありません。

## ファイル

- `scripts/daily_news.py`: ニュース収集・重複排除・日本語要約（任意）・HTML生成
- `config/sources.json`: RSS配信元と検索キーワード
- `reports/YYYY-MM-DD.json`: 日付別の機械可読アーカイブ
- `docs/YYYY-MM-DD.html`: 日付別ニュース記事ページ
- `docs/index.html`: 最新ニュースとアーカイブへのリンク
- 最新・日付別ページの先頭に前日／翌日ボタンを表示。前後の公開済み日へ移動し、最古・最新では該当ボタンを無効化します。毎日の生成時に過去ページの翌日リンクも更新します。
- `data/history.json`: 重複除外履歴
- `.github/workflows/daily.yml`: 毎朝6:17の自動実行・6:47の予備実行・Pagesデプロイ

## ローカルで試す

```bash
pip install -r requirements.txt
python scripts/daily_news.py
python -m unittest discover -s tests -v
```

**注意:** RSS配信元の変更・アクセス制限、ニュースの欠落、API制限が発生する場合があります。公開ニュースの著作権に配慮し、記事本文は転載せず短い概要とリンクのみを掲載します。
