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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import daily_news as news

class NewsTests(unittest.TestCase):
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

if __name__=='__main__': unittest.main()