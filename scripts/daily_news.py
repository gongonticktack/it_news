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
:root{color-scheme:dark;--bg:#090917;--panel:#151329;--panel2:#201b39;--line:rgba(183,162,255,.19);--text:#f5f2ff;--muted:#b2aecb;--accent:#bba2ff;--blue:#8098ff;--pink:#e5b3ff;--glow:rgba(135,96,255,.22)}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:var(--text);font:15px/1.85 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans JP",sans-serif;background:radial-gradient(ellipse 75% 40% at 7% -10%,rgba(117,77,236,.32),transparent 80%),radial-gradient(ellipse 55% 35% at 100% 12%,rgba(71,96,237,.22),transparent 80%),#090917;min-height:100vh;letter-spacing:.012em}body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.22;background-image:linear-gradient(rgba(171,150,255,.09) 1px,transparent 1px),linear-gradient(90deg,rgba(171,150,255,.09) 1px,transparent 1px);background-size:52px 52px;mask-image:linear-gradient(to bottom,#000,transparent 82%);z-index:-1}
a{color:#c8b7ff;text-decoration:none;transition:color .2s}a:hover{color:#e9ddff}a:focus-visible{outline:2px solid #d7c6ff;outline-offset:4px;border-radius:5px}
header{position:sticky;top:0;z-index:20;background:rgba(12,11,29,.78);border-bottom:1px solid var(--line);backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px)}
.top{max-width:1120px;margin:auto;display:flex;align-items:center;justify-content:space-between;gap:14px;padding:15px 24px}
.brand{color:#fff;font-size:1.12rem;letter-spacing:.07em;font-weight:850}.brand b{color:var(--accent)}nav{display:flex;gap:10px;align-items:center}nav a{font-size:.84rem;color:#d5cfee;padding:7px 12px;border:1px solid rgba(178,154,255,.17);border-radius:12px;background:rgba(255,255,255,.035)}
.wrap,main{max-width:1120px;margin:0 auto;padding:30px 24px 90px}
.hero{position:relative;overflow:hidden;margin:12px 0 28px;padding:clamp(28px,5vw,58px);border:1px solid rgba(184,157,255,.28);border-radius:28px;background:linear-gradient(115deg,rgba(108,73,208,.25),rgba(51,56,122,.19) 60%,rgba(150,104,234,.13));box-shadow:inset 0 1px rgba(255,255,255,.12),0 28px 85px rgba(0,0,0,.25);backdrop-filter:blur(22px)}
.hero:after{content:"";position:absolute;width:330px;height:330px;border-radius:50%;background:radial-gradient(circle,rgba(180,125,255,.28),transparent 68%);right:-100px;top:-140px;pointer-events:none}
.eyebrow,.kicker{font-size:.73rem;font-weight:800;letter-spacing:.2em;color:#c9b7ff;text-transform:uppercase}
.hero h1{font-size:clamp(2.5rem,7vw,5.2rem);letter-spacing:-.055em;line-height:1.12;margin:12px 0 14px;font-weight:850;background:linear-gradient(110deg,#fff 15%,#d9c2ff 65%,#9baaff);color:transparent;background-clip:text;-webkit-background-clip:text}
.hero p{max-width:660px;color:#c9c5df;margin:10px 0 0;font-size:1.01rem}
.story,.stock-card,.glass,.archive,.report-table{border:1px solid var(--line);background:linear-gradient(135deg,rgba(41,34,70,.83),rgba(20,20,42,.87));border-radius:23px;box-shadow:inset 1px 1px rgba(255,255,255,.07),0 14px 35px rgba(0,0,0,.2);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px)}
.story,.stock-card,.glass{padding:clamp(20px,3vw,30px);transition:transform .2s,border-color .2s,box-shadow .2s}
.story:hover,.stock-card:hover,.glass:hover{transform:translateY(-3px);border-color:rgba(193,169,255,.48);box-shadow:0 22px 48px rgba(23,15,58,.42)}
.news{display:grid;gap:18px}.story:first-child{background:linear-gradient(125deg,rgba(83,60,146,.68),rgba(25,24,54,.92));border-color:rgba(195,166,255,.44)}
.meta{display:flex;flex-wrap:wrap;align-items:center;gap:10px;color:var(--muted);font-size:.78rem}
.tag{border:1px solid rgba(192,168,255,.38);border-radius:100px;background:rgba(155,117,255,.15);padding:3px 11px;color:#dfceff;font-weight:750}
.story h2{font-size:clamp(1.14rem,2.6vw,1.55rem);line-height:1.48;letter-spacing:-.02em;margin:17px 0 12px;color:#fff}
.story p{color:#d1cde2;margin:11px 0}.why{border-left:3px solid #a58cff;padding-left:15px;color:#e2d9ff!important;background:linear-gradient(90deg,rgba(158,118,255,.09),transparent);padding-top:7px;padding-bottom:7px}
.link,.cta{display:inline-flex;align-items:center;gap:8px;margin-top:10px;border-radius:13px;padding:10px 17px;background:linear-gradient(115deg,#a88bff,#849cff);color:#100d25;font-weight:800;box-shadow:0 8px 25px rgba(110,91,231,.24)}.link:hover,.cta:hover{color:#0b0820;filter:brightness(1.12)}
.archive{margin-top:36px;padding:24px}.archive h2{margin:0 0 15px;font-size:1.06rem;letter-spacing:.07em}.archive ul{margin:0;padding:0;list-style:none;display:grid;gap:10px}.archive li a{display:block;padding:12px 15px;border-radius:13px;background:rgba(160,138,255,.07);border:1px solid rgba(190,166,255,.1)}.archive li a:hover{background:rgba(165,139,255,.16)}
footer{margin-top:50px;border-top:1px solid var(--line);padding-top:20px;font-size:.78rem;color:var(--muted)}
@media(max-width:620px){.top{padding:12px 15px}.wrap,main{padding:20px 14px 70px}.hero{border-radius:21px;padding:26px 22px}.story,.stock-card,.glass{border-radius:19px}nav{gap:5px}nav a{padding:6px 9px;font-size:.75rem}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{scroll-behavior:auto!important;transition:none!important;animation:none!important}}

.report{max-width:1040px}.report>h1{font-size:clamp(1.75rem,4vw,3rem);line-height:1.3;letter-spacing:-.04em;margin:18px 0 8px}.report>h2{margin:42px 0 18px;padding-bottom:11px;border-bottom:1px solid rgba(190,164,255,.26);font-size:clamp(1.3rem,3vw,1.75rem);letter-spacing:-.02em}.report>h3{font-size:1.16rem}
.report p,.report li{color:#d4d0e6}.report p{margin:13px 0}.report strong{color:#f5f0ff}.report>p:first-of-type{color:#cbbaff}
.report table{width:100%;border-collapse:separate;border-spacing:0;border:1px solid var(--line);border-radius:17px;overflow:hidden;display:block;overflow-x:auto;background:rgba(30,25,54,.72)}.report th,.report td{padding:11px 14px;border-bottom:1px solid rgba(180,160,255,.13);text-align:left;white-space:nowrap}.report th{color:#d9c7ff;background:rgba(137,107,221,.19);font-size:.82rem}.report tr:last-child td{border-bottom:0}.report pre{overflow-x:auto;padding:15px;border-radius:14px;background:#101126;border:1px solid var(--line);color:#d5cbff}
.report .stock-card{margin:20px 0;padding:23px 25px}.stock-card h3{margin:0 0 10px;font-size:1.28rem;color:#f2eaff}.stock-card>p:nth-of-type(2){background:rgba(131,109,238,.11);padding:12px 15px;border-radius:12px;border:1px solid rgba(166,144,255,.15)}.stock-card ul{padding-left:20px}.stock-card a{overflow-wrap:anywhere}
.archive-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:15px}.archive-grid .glass{display:block;color:#f3efff}.archive-grid .glass span{display:block;font-size:.75rem;color:#b9addb;letter-spacing:.13em;margin-bottom:8px}.archive-grid .glass strong{font-size:1.15rem}.archive-grid .glass small{display:block;color:#c4bfd9;margin-top:8px}
.report hr{border:0;border-top:1px solid var(--line);margin-top:35px}.report>ul{padding-left:23px}

.news{grid-template-columns:1fr}.story h2{max-width:830px}.story .meta time{margin-left:auto}.story .link{margin-top:16px}@media(min-width:850px){.news .story:first-child{padding:38px 40px}.news .story:first-child h2{font-size:1.95rem}}
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