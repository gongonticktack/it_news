#!/usr/bin/env python3
"""Collect recent AI/IT RSS headlines and publish a dated, public GitHub Pages archive."""
from __future__ import annotations

import calendar
import html
import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

import feedparser
import requests

ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo('Asia/Tokyo')
LOG = logging.getLogger('it_news')
HEADERS = {'User-Agent': 'ITNewsDigest/1.0 (+https://github.com/gongonticktack/it_news)'}
TOPICS = {
    'AI・LLM': ['ai ', '人工知能', '生成ai', '生成 ai', 'llm', 'モデル', 'model', 'gpt', 'gemini', 'claude', 'qwen', 'deepseek', 'agent', 'エージェント'],
    '開発・OSS': ['github', 'open source', 'オープンソース', 'developer', '開発者', 'python', 'rust', 'copilot', 'sdk', 'api', 'oss'],
    'クラウド': ['aws', 'cloudflare', 'cloud', 'クラウド', 'azure', 'gcp', 'kubernetes', 'serverless'],
    'セキュリティ': ['security', 'セキュリティ', '脆弱性', 'vulnerability', 'cyber', 'サイバー'],
    '3D・ロボット': ['robot', 'ロボット', '3d', 'simulation', 'シミュレーション', 'nvidia', 'gpu', '画像生成', 'vision'],
}
IMPORTANT = ['launch', 'release', 'announc', 'introduc', 'open source', 'generally available', '公開', '発表', '提供開始', 'リリース', '新モデル', '新機能', '重大', 'security update']


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return fallback


def normalize_title(value: str) -> str:
    value = re.sub(r'\s+[-–|]\s+[^-–|]{2,35}$', '', value)
    return re.sub(r'[^\w\u3040-\u30ff\u4e00-\u9fff]+', '', value.casefold())


def clean_text(value: str, limit=600) -> str:
    value = re.sub(r'<[^>]+>', ' ', value or '')
    value = html.unescape(value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value[:limit]


def published(entry):
    for key in ('published_parsed', 'updated_parsed'):
        value = entry.get(key)
        if value:
            try:
                return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
            except (ValueError, OverflowError, TypeError):
                continue
    return None


def fetch_feed(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=14)
        resp.raise_for_status()
        return feedparser.parse(resp.content).entries
    except Exception as exc:
        LOG.warning('RSS unavailable: %s (%s)', url, exc)
        return []


def news_url(query: str):
    return 'https://news.google.com/rss/search?' + urlencode({'q': query + ' when:7d', 'hl': 'ja', 'gl': 'JP', 'ceid': 'JP:ja'})


def score(item, now):
    title = item['title'].casefold()
    summary = item['summary'].casefold()
    combined = title + ' ' + summary
    age_h = max(0, (now - item['published_dt']).total_seconds() / 3600)
    points = 3 * item['priority'] + max(0, 15 - age_h / 7)
    points += 4 * sum(any(word in combined for word in words) for words in TOPICS.values())
    points += 5 if any(word in combined for word in IMPORTANT) else 0
    points += 4 if item['official'] else 0
    return points


def topic_of(item):
    text = (item['title'] + ' ' + item['summary']).casefold()
    return next((name for name, words in TOPICS.items() if any(w in text for w in words)), 'IT全般')


def canonical_url(url):
    # Do not follow redirects, as it may expose requests to untrusted hosts.
    parsed = urlparse(url)
    if parsed.scheme not in ('https', 'http') or not parsed.netloc:
        return ''
    return parsed._replace(query='', fragment='').geturl().rstrip('/')


def similar(a, b):
    x, y = normalize_title(a), normalize_title(b)
    return bool(x and y and (x == y or (min(len(x), len(y)) >= 18 and SequenceMatcher(None, x, y).ratio() >= .83)))


def gather(config, now):
    candidates = []
    sources = [(f['name'], f['url'], int(f.get('priority', 2)), True) for f in config['feeds']]
    sources += [('Googleニュース', news_url(q), 2, False) for q in config['google_news_queries']]
    for source, url, priority, official in sources:
        entries = fetch_feed(url)
        LOG.info('%s: %s items', source, len(entries))
        for entry in entries[:45]:
            dt = published(entry)
            if not dt:
                continue
            age = now - dt
            if age < timedelta(hours=-2) or age > timedelta(days=7):
                continue
            title = clean_text(entry.get('title', ''), 220)
            link = canonical_url(entry.get('link', ''))
            if not title or not link:
                continue
            summary = clean_text(entry.get('summary', ''), 500)
            if not official:
                # Google News RSS titles normally include the outlet after ' - '.
                outlet = title.rsplit(' - ', 1)[-1] if ' - ' in title else source
                if outlet != source:
                    title = title.rsplit(' - ', 1)[0]
                source_name = outlet
            else:
                source_name = source
            item = {'title': title, 'summary': summary, 'url': link,
                    'source': source_name, 'published': dt.astimezone(JST).strftime('%Y-%m-%d %H:%M JST'),
                    'published_dt': dt, 'official': official, 'priority': priority}
            item['score'] = score(item, now)
            item['topic'] = topic_of(item)
            candidates.append(item)
    return sorted(candidates, key=lambda i: (i['score'], i['published_dt']), reverse=True)


def recent_items(history, today, days=14):
    old = []
    for d, items in history.items():
        try:
            age = (today - date.fromisoformat(d)).days
        except (ValueError, TypeError):
            continue
        if 0 <= age <= days:
            old.extend(items)
    return old


def select(candidates, history, today, now, limit=5):
    old = recent_items(history, today)
    result = []
    for window in (timedelta(hours=72), timedelta(days=7)):
        for item in candidates:
            if now - item['published_dt'] > window:
                continue
            if any(item['url'] == x.get('url') or similar(item['title'], x.get('title', '')) for x in old + result):
                continue
            # Prefer variety without banning multiple major developments from one outlet.
            if sum(x['source'] == item['source'] for x in result) >= 2:
                continue
            result.append(item)
            if len(result) == limit:
                return result
    return result


def ai_enrich(items):
    key = os.getenv('OPENAI_API_KEY', '').strip()
    if not key or not items:
        return items
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, timeout=45)
        source = [{'id': i, 'title': x['title'], 'summary': x['summary'], 'source': x['source']}
                  for i, x in enumerate(items)]
        resp = client.responses.create(
            model=os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
            instructions=('あなたは日本語のITニュース編集者です。提供されたRSS見出しと概要だけを根拠に、各項目の日本語の見出し（最大55文字）、概要（最大120文字）、なぜ重要か（最大100文字）を作成。'
                          '事実未確認の推測・数字の創作は禁止。情報不足ならその旨を記載。必ず入力のidに対応するJSON配列だけを返す。'
                          'JSON各要素はid,title_ja,overview,importanceの4キー。ニュース本文を読んだと主張しない。'),
            input=json.dumps(source, ensure_ascii=False),
            max_output_tokens=1800,
        )
        content = resp.output_text.strip()
        content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content)
        data = json.loads(content)
        by_id = {row['id']: row for row in data if isinstance(row, dict) and isinstance(row.get('id'), int)}
        for i, item in enumerate(items):
            row = by_id.get(i, {})
            item['title_ja'] = clean_text(row.get('title_ja', ''), 100) or item['title']
            item['overview'] = clean_text(row.get('overview', ''), 250) or item['summary']
            item['importance'] = clean_text(row.get('importance', ''), 220) or '最新の動向として注目されます。'
    except Exception as exc:
        LOG.warning('OpenAI summary failed; using RSS descriptions: %s', exc)
    return items


CSS = '''
:root{color-scheme:dark;--bg:#090f1b;--panel:#111c2b;--line:#27384c;--text:#eef4ff;--muted:#a2b4c8;--accent:#6dd9c1}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,"Noto Sans JP",sans-serif;line-height:1.7}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}.wrap{max-width:960px;margin:auto;padding:22px 20px 80px}
header{border-bottom:1px solid var(--line);background:#0c1625}.top{max-width:960px;margin:auto;display:flex;align-items:center;justify-content:space-between;padding:18px 20px;gap:12px}
.brand{font-size:1.35rem;letter-spacing:.11em;font-weight:900;color:#fff}.brand b{color:var(--accent)}nav{display:flex;gap:20px;font-size:.92rem}
.eyebrow{color:var(--accent);font-size:.75rem;font-weight:800;letter-spacing:.2em;margin-top:38px}.hero h1{font-size:clamp(2.2rem,7vw,4.5rem);line-height:1.14;letter-spacing:-.035em;margin:10px 0}.hero p{color:var(--muted);margin-bottom:30px}
.news{display:grid;gap:18px}.story{background:var(--panel);border:1px solid var(--line);border-radius:17px;padding:24px;box-shadow:0 12px 35px #0002}.story:first-child{border-color:#396d6a}
.meta{display:flex;gap:10px;align-items:center;flex-wrap:wrap;color:var(--muted);font-size:.78rem}.tag{border:1px solid #376b64;color:var(--accent);border-radius:50px;padding:2px 10px;font-weight:700}
.story h2{font-size:clamp(1.15rem,3.3vw,1.5rem);line-height:1.4;margin:15px 0 10px}.story p{margin:10px 0;color:#d8e2ef}.why{border-left:3px solid var(--accent);padding-left:12px;color:#c0f0e4!important}
.link{display:inline-block;margin-top:8px;font-weight:700}.archive{margin-top:40px;border-top:1px solid var(--line);padding-top:18px}.archive ul{padding-left:18px}.archive li{margin:8px 0}footer{color:var(--muted);font-size:.78rem;border-top:1px solid var(--line);margin-top:48px;padding-top:20px}
@media(max-width:520px){.story{padding:18px}.top{align-items:flex-start}.hero h1{margin-top:8px}nav{gap:12px}}
'''


def esc(s):
    return html.escape(str(s), quote=True)


def page_shell(title, content):
    return ('<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="description" content="AI・ITニュースを毎朝5件、日本語で紹介する日刊アーカイブ">'
            '<title>' + esc(title) + '</title><style>' + CSS + '</style><link rel="stylesheet" href="theme.css"></head><body>'
            '<header><div class="top"><a class="brand" href="index.html">IT <b>NEWS</b></a><nav><a href="index.html">最新号</a><a href="index.html#archive">過去の記事</a></nav></div></header>'
            '<main class="wrap">' + content + '<footer>RSSの見出し・概要をもとに編集。元記事を必ずご確認ください。'
            'GitHub Actionsの起動時刻は前後することがあります。</footer></main></body></html>')


def story_html(item, number):
    title = esc(item.get('title_ja') or item['title'])
    overview = esc(item.get('overview') or item.get('summary') or '概要は配信されていません。')
    why = item.get('importance')
    why_html = '<p class="why"><strong>なぜ重要か：</strong>' + esc(why) + '</p>' if why else '<p class="why">なぜ重要か：元記事で詳細をご確認ください。</p>'
    return (f'<article class="story"><div class="meta"><span class="tag">{esc(item["topic"])}</span>'
            f'<span>#{number:02d}</span><span>{esc(item["source"])}</span><time>{esc(item["published"])}</time></div>'
            f'<h2>{title}</h2><p>{overview}</p>{why_html}'
            f'<a class="link" href="{esc(item["url"])}" target="_blank" rel="noopener noreferrer">情報源・元記事を読む ↗</a></article>')


def render_day(day, items):
    headline = f'<section class="hero"><div class="eyebrow">DAILY TECHNOLOGY BRIEFING</div><h1 class="gradient-text">IT NEWS.</h1><p>{esc(day)} ｜ AI・ITニュース {len(items)}件</p></section>'
    stories = '<section class="news">' + (''.join(story_html(x, i) for i, x in enumerate(items, 1)) or '<div class="story">新しいニュースを取得できませんでした。前号をご覧ください。</div>') + '</section>'
    return page_shell(f'IT NEWS｜{day}', headline + stories)


def render_index(day, items, archive_days):
    headline = f'<section class="hero"><div class="eyebrow">YOUR DAILY TECH SIGNAL</div><h1 class="gradient-text">IT NEWS.</h1><p>AI・クラウド・開発技術の重要ニュースを毎朝5件。<br>最新号：{esc(day)}</p></section>'
    stories = '<section class="news">' + (''.join(story_html(x, i) for i, x in enumerate(items, 1)) or '<div class="story">現在、新しいニュースはありません。</div>') + '</section>'
    archive = '<section class="archive" id="archive"><h2>ARCHIVE / 過去の記事</h2><ul>' + ''.join(f'<li><a href="{esc(d)}.html">{esc(d)} のIT NEWS</a></li>' for d in archive_days) + '</ul></section>'
    return page_shell('IT NEWS｜最新のAI・ITニュース', headline + stories + archive)


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    now = datetime.now(JST)
    today = date.fromisoformat(os.environ['REPORT_DATE']) if os.getenv('REPORT_DATE') else now.date()
    config = read_json(ROOT / 'config/sources.json', {})
    if not config.get('feeds'):
        raise RuntimeError('Missing config/sources.json')
    history_path = ROOT / 'data/history.json'
    history = read_json(history_path, {})
    candidates = gather(config, now)
    if not candidates:
        raise RuntimeError('All news feeds failed or contained no dated recent entries; refusing to overwrite site')
    items = ai_enrich(select(candidates, history, today, now))
    fallback_importance = {
        'AI・LLM': 'AIモデルの性能・利用方法や導入判断に関わる可能性があります。',
        '開発・OSS': '開発者が使えるツールや技術の選択肢に関わる可能性があります。',
        'クラウド': 'クラウドサービスの機能や運用方法に関わる可能性があります。',
        'セキュリティ': 'システムの安全性や対策の見直しに関わる可能性があります。',
        '3D・ロボット': '画像処理・3D・ロボティクスの技術動向を知る手掛かりになります。',
        'IT全般': 'IT分野の最新動向として確認する価値があります。',
    }
    for item in items:
        item.setdefault('importance', fallback_importance.get(item['topic'], fallback_importance['IT全般']))
        item.pop('published_dt', None)
        item.pop('score', None)
        item.pop('priority', None)
        item.pop('official', None)
    (ROOT / 'docs').mkdir(exist_ok=True)
    (ROOT / 'reports').mkdir(exist_ok=True)
    (ROOT / 'data').mkdir(exist_ok=True)
    day = today.isoformat()
    (ROOT / 'reports' / f'{day}.json').write_text(json.dumps({'date': day, 'items': items}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (ROOT / 'docs' / f'{day}.html').write_text(render_day(day, items), encoding='utf-8')
    history[day] = [{'title': x['title'], 'url': x['url']} for x in items]
    history = {k: v for k, v in history.items() if (today - date.fromisoformat(k)).days <= 30}
    history_path.write_text(json.dumps(dict(sorted(history.items())), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    archive_days = sorted((p.stem for p in (ROOT / 'reports').glob('????-??-??.json')), reverse=True)
    (ROOT / 'docs' / 'index.html').write_text(render_index(day, items, archive_days), encoding='utf-8')
    print(f'IT NEWS {day}: {len(items)} stories; {len(candidates)} candidate headlines')


if __name__ == '__main__':
    main()