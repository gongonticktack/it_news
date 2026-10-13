#!/usr/bin/env python3
"""Build missing retrospective news editions without replacing existing reports."""
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
import daily_news as news

ROOT = Path(__file__).resolve().parents[1]

def run():
    config_path = ROOT/'config/backfill.json'
    if not config_path.exists():
        return
    config = json.loads(config_path.read_text())
    day = date.fromisoformat(config['start'])
    end = min(date.fromisoformat(config['end']), datetime.now(news.JST).date()-timedelta(days=1))
    previous_date = os.environ.get('REPORT_DATE')
    try:
        while day <= end:
            path = ROOT/'reports'/f'{day}.json'
            if not path.exists():
                os.environ['REPORT_DATE'] = day.isoformat()
                news.main()
                print(f'Reconstructed {day}', flush=True)
            day += timedelta(days=1)
    finally:
        if previous_date is None:
            os.environ.pop('REPORT_DATE', None)
        else:
            os.environ['REPORT_DATE'] = previous_date

if __name__ == '__main__':
    run()
