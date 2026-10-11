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



def needs_japanese_translation(value: str) -> bool:
    """Translate English prose; keep Japanese text and short product names unchanged."""
    text = clean_text(value, 500)
    return bool(re.search(r'[A-Za-z]{4}', text) and not re.search(r'[\u3040-\u30ff\u3400-\u9fff]', text))


KNOWN_TRANSLATIONS = {
    'Introducing Clef-omni with full multimodality, plus a faster Clef and a cheaper Clef-flash':
        'マルチモーダル対応の「Clef-omni」を発表、既存モデルの高速化と低価格版も提供',
    'We are expanding the Clef decision model family with Clef-omni, natively processing audio, video, images, and text in a single pipeline. We’ve also lowered Clef-flash pricing and boosted Clef inference speeds by up to 2.0x.':
        'Clefの意思決定モデル群に、音声・動画・画像・テキストを単一の処理系で扱う「Clef-omni」が加わりました。Clef-flashの料金引き下げと、推論速度の最大2倍への改善も発表されています。',
    'Introducing on-demand CPU and memory profiling with flamegraphs for Workers and Durable Objects':
        'Cloudflare WorkersとDurable ObjectsにCPU・メモリのオンデマンド解析機能を追加',
    'On-demand CPU and memory profiling for Cloudflare Workers and Durable Objects is now available. Now you can generate interactive flamegraphs directly in production to quickly track down memory leaks and performance bottlenecks.':
        'Cloudflare WorkersとDurable Objectsで、CPUとメモリの使用状況を本番環境から直接解析できるようになりました。対話型フレームグラフを生成し、メモリリークや性能上のボトルネックの特定を支援します。',
    'Hack the World: Why hackathons are still the best place to learn to build':
        '「Hack the World」：開発を学ぶ場としてハッカソンが今も有効な理由',
    'The barriers to building software have collapsed. Today, anyone can build. Hackathons are a great place to start learning. The post Hack the World: Why hackathons are still the best place to learn to build appeared first on The GitHub Blog .':
        'ソフトウェア開発を始めるための障壁が下がるなか、ハッカソンは実践的な開発を学ぶ入り口として有効だとするGitHub Blogの記事です。',
}


def translate_to_japanese(value: str, cache: dict) -> str:
    """Free, best-effort translation when the optional OpenAI API is not configured."""
    value = clean_text(value, 500)
    if not value or not needs_japanese_translation(value):
        return value
    if value in KNOWN_TRANSLATIONS:
        return KNOWN_TRANSLATIONS[value]
    if value in cache:
        return cache[value]
    translated = ''
    try:
        from deep_translator import GoogleTranslator
        translated = GoogleTranslator(source='auto', target='ja').translate(value) or ''
    except Exception as exc:
        LOG.warning('Google translation unavailable: %s', exc)
    if needs_japanese_translation(translated) or not translated:
        try:
            from deep_translator import MyMemoryTranslator
            translated = MyMemoryTranslator(source='english', target='japanese').translate(value[:450]) or ''
        except Exception as exc:
            LOG.warning('Backup translation unavailable: %s', exc)
    translated = clean_text(translated, 650)
    if not needs_japanese_translation(translated) and translated:
        cache[value] = translated
        return translated
    LOG.warning('Unable to translate headline or summary: %s', value[:90])
    return ''


def has_japanese(value: str) -> bool:
    return bool(re.search(r'[\u3040-\u30ff\u3400-\u9fff]', value or ''))


def translate_to_english(value: str, cache: dict) -> str:
    value = clean_text(value, 500)
    if not value or not has_japanese(value):
        return value
    if value in cache:
        return cache[value]
    try:
        from deep_translator import GoogleTranslator
        translated = clean_text(GoogleTranslator(source='auto', target='en').translate(value) or '', 650)
        if translated and not has_japanese(translated):
            cache[value] = translated
            return translated
    except Exception as exc:
        LOG.warning('English translation unavailable: %s', exc)
    return ''


def localize_items(items: list[dict]) -> list[dict]:
    """Keep original RSS text and prepare both languages for every article."""
    cache_ja, cache_en = {}, {}
    for item in items:
        original_title = clean_text(item.get('title', ''), 220)
        original_summary = clean_text(item.get('summary', ''), 500)
        prior_title = clean_text(item.get('title_ja') or '', 220)
        prior_overview = clean_text(item.get('overview') or '', 500)

        # Old editions may contain a generic placeholder after a failed translation.
        if item.get('translation_note') or prior_title.startswith('海外ITニュース：'):
            prior_title = ''
        if prior_overview.startswith(('配信元の概要は', '配信元から記事の概要が')):
            prior_overview = ''

        title_ja = prior_title or translate_to_japanese(original_title, cache_ja)
        overview_ja = prior_overview or translate_to_japanese(original_summary, cache_ja)
        title_en = translate_to_english(original_title, cache_en)
        overview_en = translate_to_english(original_summary, cache_en)

        item['title_ja'] = title_ja or original_title
        item['overview'] = overview_ja or original_summary or '配信元から記事の概要が提供されていません。'
        item['title_en'] = title_en or original_title
        item['overview_en'] = overview_en or original_summary or 'No summary was provided by the source.'
        item.pop('translation_note', None)
        item.pop('translation_note_en', None)

        if original_title and not title_ja and needs_japanese_translation(original_title):
            item['translation_note'] = '自動翻訳に失敗したため、見出しは英語の原文を表示しています。'
        if original_summary and not overview_ja and needs_japanese_translation(original_summary):
            item['summary_translation_note'] = '概要を翻訳できなかったため、英語の原文を表示しています。'
        else:
            item.pop('summary_translation_note', None)
        if has_japanese(original_title) and not title_en:
            item['translation_note_en'] = 'English translation unavailable. Showing the original Japanese headline.'
        if has_japanese(original_summary) and not overview_en:
            item['summary_translation_note_en'] = 'English translation unavailable. Showing the original Japanese summary.'
        else:
            item.pop('summary_translation_note_en', None)
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
/* Accessible bilingual toggle and localized content */
.lang-en{display:none}
html[lang="en"] .lang-ja{display:none}
html[lang="en"] .lang-en{display:inline}
html[lang="en"] p.lang-en,html[lang="en"] div.lang-en{display:block}
.lang-switch{display:inline-flex;align-items:center;gap:3px;padding:4px;border-radius:100px;background:rgba(148,119,239,.13);border:1px solid rgba(188,165,255,.35);white-space:nowrap}
.lang-switch button{appearance:none;cursor:pointer;border:0;background:transparent;color:#c7bedf;border-radius:100px;padding:6px 12px;font:700 .79rem/1.2 inherit;min-width:43px;transition:background .18s,color .18s,box-shadow .18s}
.lang-switch button[aria-pressed="true"]{color:#110d28;background:linear-gradient(110deg,#d4b8ff,#a4aaff);box-shadow:0 3px 12px rgba(118,86,218,.24)}
.lang-switch button:focus-visible{outline:2px solid #d9c5ff;outline-offset:3px}
.translation-note{font-size:.82rem!important;color:#dfc1ff!important;background:rgba(181,128,255,.11);border:1px solid rgba(191,156,255,.25);padding:8px 12px;border-radius:10px}
@media(max-width:620px){.top{flex-wrap:wrap}.top nav{margin-left:auto}.lang-switch button{padding:7px 10px}}

'''


def esc(s):
    return html.escape(str(s), quote=True)


def bilingual(ja, en):
    return '<span class="lang-ja">' + esc(ja) + '</span><span class="lang-en">' + esc(en) + '</span>'


def page_shell(title, content):
    script = """
<script>
(function(){
  const buttons = Array.from(document.querySelectorAll('[data-set-lang]'));
  function setLanguage(lang) {
    lang = lang === 'en' ? 'en' : 'ja';
    document.documentElement.lang = lang;
    buttons.forEach(function(button) {
      button.setAttribute('aria-pressed', String(button.dataset.setLang === lang));
    });
    try { localStorage.setItem('it_news_language', lang); } catch (error) {}
  }
  let saved = 'ja';
  try { saved = localStorage.getItem('it_news_language') || 'ja'; } catch (error) {}
  setLanguage(saved);
  buttons.forEach(function(button) {
    button.addEventListener('click', function() { setLanguage(button.dataset.setLang); });
  });
})();
</script>"""
    nav = ('<header><div class="top">'
           '<a class="brand" href="index.html">IT <b>NEWS</b></a>'
           '<nav><a href="index.html">' + bilingual('最新号', 'Latest') + '</a>'
           '<a href="index.html#archive">' + bilingual('過去の記事', 'Archive') + '</a></nav>'
           '<div class="lang-switch" role="group" aria-label="Language / 表示言語">'
           '<button type="button" data-set-lang="ja" aria-pressed="true" lang="ja">日本語</button>'
           '<button type="button" data-set-lang="en" aria-pressed="false" lang="en">EN</button>'
           '</div></div></header>')
    footer = bilingual(
        'RSSの見出し・概要をもとに編集。元記事を必ずご確認ください。GitHub Actionsの起動時刻は前後することがあります。',
        'Edited from RSS headlines and summaries. Please verify the original sources. GitHub Actions may run later than scheduled.'
    )
    return ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="description" content="Daily AI and IT news in Japanese and English">'
            '<title>' + esc(title) + '</title><style>' + CSS + '</style>'
            '<link rel="stylesheet" href="theme.css"></head><body>'
            + nav + '<main class="wrap">' + content + '<footer>' + footer + '</footer>'
            '</main>' + script + '</body></html>')


IMPORTANCE_EN = {
    'AI・LLM': 'This development may affect AI capabilities, usage, and adoption decisions.',
    '開発・OSS': 'This may influence developers’ choices of tools and technologies.',
    'クラウド': 'This may affect cloud capabilities and operations.',
    'セキュリティ': 'This may inform security practices and risk management.',
    '3D・ロボット': 'This may be relevant to imaging, 3D, and robotics technologies.',
    'IT全般': 'This is a development worth following in the technology sector.',
}


def story_html(item, number):
    title = bilingual(item.get('title_ja') or item['title'],
                      item.get('title_en') or item['title'])
    overview = bilingual(item.get('overview') or item.get('summary') or '概要は配信されていません。',
                         item.get('overview_en') or item.get('summary') or 'No summary was provided.')
    why = item.get('importance') or '元記事で詳細をご確認ください。'
    why_en = item.get('importance_en') or IMPORTANCE_EN.get(item.get('topic'), IMPORTANCE_EN['IT全般'])
    why_html = ('<p class="why"><strong>' + bilingual('なぜ重要か：', 'Why it matters: ') +
                '</strong>' + bilingual(why, why_en) + '</p>')
    note_ja = ' '.join(filter(None, [item.get('translation_note'), item.get('summary_translation_note')]))
    note_en = ' '.join(filter(None, [item.get('translation_note_en'), item.get('summary_translation_note_en')]))
    note_html = ''
    if note_ja:
        note_html += '<p class="translation-note lang-ja">' + esc(note_ja) + '</p>'
    if note_en:
        note_html += '<p class="translation-note lang-en">' + esc(note_en) + '</p>'
    return (f'<article class="story"><div class="meta"><span class="tag">{esc(item["topic"])}</span>'
            f'<span>#{number:02d}</span><span>{esc(item["source"])}</span><time>{esc(item["published"])}</time></div>'
            f'<h2>{title}</h2><p>{overview}</p>{why_html}{note_html}'
            f'<a class="link" href="{esc(item["url"])}" target="_blank" rel="noopener noreferrer">'
            + bilingual('情報源・元記事を読む ↗', 'Read original source ↗') + '</a></article>')


def render_day(day, items):
    headline = ('<section class="hero"><div class="eyebrow">DAILY TECHNOLOGY BRIEFING</div>'
                '<h1 class="gradient-text">IT NEWS.</h1><p>' + esc(day) + ' ｜ ' +
                bilingual(f'AI・ITニュース {len(items)}件', f'{len(items)} AI & technology stories') +
                '</p></section>')
    stories = ('<section class="news">' +
               (''.join(story_html(x, i) for i, x in enumerate(items, 1)) or
                '<div class="story">' + bilingual('新しいニュースを取得できませんでした。', 'No news available.') + '</div>') +
               '</section>')
    return page_shell(f'IT NEWS｜{day}', headline + stories)


def render_index(day, items, archive_days):
    headline = ('<section class="hero"><div class="eyebrow">YOUR DAILY TECH SIGNAL</div>'
                '<h1 class="gradient-text">IT NEWS.</h1><p>' +
                bilingual('AI・クラウド・開発技術の重要ニュースを毎朝5件。',
                          'Five important AI, cloud, and software development stories each morning.') +
                ' ' + bilingual('最新号：', 'Latest edition: ') + esc(day) + '</p></section>')
    stories = ('<section class="news">' +
               (''.join(story_html(x, i) for i, x in enumerate(items, 1)) or
                '<div class="story">' + bilingual('現在、新しいニュースはありません。', 'No new stories available.') + '</div>') +
               '</section>')
    archive = ('<section class="archive" id="archive"><h2>ARCHIVE / ' +
               bilingual('過去の記事', 'Previous editions') + '</h2><ul>' +
               ''.join('<li><a href="' + esc(d) + '.html">' +
                       bilingual(d + ' のIT NEWS', 'IT NEWS — ' + d) + '</a></li>'
                       for d in archive_days) + '</ul></section>')
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
    existing_report = read_json(ROOT / 'reports' / f'{today.isoformat()}.json', {})
    if existing_report.get('items'):
        # Rebuild today's edition without changing the selected stories on code updates.
        items = existing_report['items']
        candidates = []
        LOG.info('Reusing %s existing stories for %s', len(items), today)
    else:
        candidates = gather(config, now)
        if not candidates:
            raise RuntimeError('All news feeds failed or contained no dated recent entries; refusing to overwrite site')
        items = ai_enrich(select(candidates, history, today, now))
    items = localize_items(items)
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