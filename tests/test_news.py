import sys
import types
try:
    import feedparser
except ImportError:
    sys.modules["feedparser"] = types.ModuleType("feedparser")
try:
    import requests
except ImportError:
    sys.modules["requests"] = types.ModuleType("requests")
import unittest
import tempfile
import json
from unittest.mock import patch
from bs4 import BeautifulSoup
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import daily_news as news

class NewsTests(unittest.TestCase):
    def test_date_navigation_and_archived_page_rebuild(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'reports').mkdir()
            days = ['2026-10-08', '2026-10-10', '2026-10-12']
            for day in days:
                (root / 'reports' / (day + '.json')).write_text(json.dumps({'date': day, 'items': []}), encoding='utf-8')
            with patch.object(news, 'ROOT', root):
                news.build_site()
                for index, day in enumerate(days):
                    page = (root / 'docs' / (day + '.html')).read_text(encoding='utf-8')
                    soup = BeautifulSoup(page, 'html.parser')
                    nav = soup.select_one('.date-navigation')
                    self.assertEqual(nav.time['datetime'], day)
                    self.assertLess(page.index('class="date-navigation"'), page.index('class="hero"'))
                    for relation, target in [('prev', days[index - 1] if index else None),
                                             ('next', days[index + 1] if index + 1 < len(days) else None)]:
                        link = nav.find('a', rel=relation)
                        if target:
                            self.assertEqual(link['href'], target + '.html')
                            self.assertTrue((root / 'docs' / link['href']).exists())
                        else:
                            self.assertIsNone(link)
                    self.assertEqual(len(nav.select('[aria-disabled="true"]')), int(index == 0) + int(index == len(days) - 1))
                latest = BeautifulSoup((root / 'docs/index.html').read_text(encoding='utf-8'), 'html.parser')
                self.assertEqual(latest.select_one('.date-navigation time')['datetime'], days[-1])
                self.assertIsNone(latest.select_one('.date-navigation a[rel="next"]'))
                self.assertEqual(latest.select_one('.date-navigation a[rel="prev"]')['href'], '2026-10-10.html')


    def test_reuses_successful_translations(self):
        item = {'title': 'New platform announcement', 'summary': 'English source summary',
                'title_ja': '新しいプラットフォームの発表', 'overview': '日本語の概要',
                'title_en': 'New platform announcement', 'overview_en': 'English source summary',
                'topic': 'IT全般', 'source': 'Official'}
        with patch.object(news, 'translate_to_english') as english, \
             patch.object(news, 'translate_to_japanese') as japanese:
            localized = news.localize_items([item])[0]
        english.assert_not_called()
        japanese.assert_not_called()
        self.assertEqual(localized['overview_en'], 'English source summary')

    def test_empty_completed_edition_is_not_collected_again(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ('config', 'reports', 'data'):
                (root / folder).mkdir()
            (root / 'config/sources.json').write_text('{"feeds": ["fixture"]}')
            (root / 'reports/2026-10-13.json').write_text('{"date":"2026-10-13","items":[]}')
            with patch.object(news, 'ROOT', root), patch.object(news, 'gather') as gather, \
                 patch.dict('os.environ', {'REPORT_DATE': '2026-10-13'}):
                news.main()
            gather.assert_not_called()
            self.assertIn('2026-10-13', (root / 'docs/index.html').read_text())
            self.assertEqual(json.loads((root / 'reports/2026-10-13.json').read_text())['items'], [])

    def test_theme_has_single_stylesheet_and_dark_browser_color(self):
        page = news.page_shell('IT NEWS', '<p>fixture</p>')
        soup = BeautifulSoup(page, 'html.parser')
        self.assertIsNone(soup.find('style'))
        self.assertEqual(soup.find('meta', attrs={'name': 'theme-color'})['content'], '#161220')
        self.assertEqual(len(soup.select('link[rel="stylesheet"]')), 1)

    def test_title_similarity(self):
        self.assertTrue(news.similar('OpenAI releases a new model', 'OpenAI releases a new model - News'))
        self.assertFalse(news.similar('Cloudflare security update', 'New robotics hardware'))

    def test_select_skips_previous(self):
        now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        item = lambda title, url: {'title':title,'url':url,'source':'Official','published_dt':now-timedelta(hours=1)}
        history = {'2026-10-11':[{'title':'New AI release','url':'https://example.com/old'}]}
        chosen = news.select([item('New AI release','https://example.com/new'),item('Robotics platform launch','https://example.com/other')],history,date(2026,10,12),now)
        self.assertEqual(len(chosen),1)
        self.assertEqual(chosen[0]['url'],'https://example.com/other')

    def test_no_undated(self):
        self.assertIsNone(news.published({'title':'undated'}))

    def test_html_escapes(self):
        item={'title':'<script>x</script>','summary':'safe','source':'Test','published':'2026-10-12 07:00 JST','url':'https://example.com','topic':'AI'}
        page=news.story_html(item,1)
        self.assertNotIn('<script>',page)
        self.assertIn('&lt;script&gt;',page)


    def test_language_toggle_and_bilingual_content(self):
        item = {'title': 'Original English headline',
                'title_ja': '日本語の見出し',
                'title_en': 'Original English headline',
                'summary': 'Original English summary',
                'overview': '日本語の概要',
                'overview_en': 'Original English summary',
                'source': 'Official', 'published': '2026-10-12 07:00 JST',
                'url': 'https://example.com', 'topic': 'AI・LLM'}
        page = news.render_day('2026-10-12', [item])
        self.assertIn('data-set-lang="ja"', page)
        self.assertIn('data-set-lang="en"', page)
        self.assertIn('Original English headline', page)
        self.assertIn('日本語の見出し', page)
        self.assertIn("localStorage.setItem('it_news_language'", page)

    def test_failed_translation_preserves_original(self):
        from unittest.mock import patch
        item = {'title': 'Original English headline', 'summary': 'English summary',
                'source': 'Official', 'topic': 'IT全般'}
        with patch.object(news, 'translate_to_japanese', return_value=''):
            result = news.localize_items([item])[0]
        self.assertEqual(result['title_ja'], 'Original English headline')
        self.assertEqual(result['overview'], 'English summary')
        self.assertIn('自動翻訳に失敗', result['translation_note'])
        self.assertIn('英語の原文', result['summary_translation_note'])
        page = news.story_html({**result, 'published': '2026-10-12 07:00 JST',
                                'url': 'https://example.com'}, 1)
        self.assertIn('Original English headline', page)
        self.assertIn('自動翻訳に失敗', page)

    def test_original_japanese_stays_available_in_english_view(self):
        from unittest.mock import patch
        item = {'title': '日本語のニュース', 'summary': '日本語の説明',
                'source': 'Official', 'topic': 'IT全般'}
        with patch.object(news, 'translate_to_english', return_value=''):
            result = news.localize_items([item])[0]
        self.assertEqual(result['title_en'], '日本語のニュース')
        self.assertIn('English translation unavailable', result['translation_note_en'])

if __name__=='__main__': unittest.main()
