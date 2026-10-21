#!/usr/bin/env python3
"""Recover each province's actual last-good pointer and dated snapshots before deploying."""
import json
import argparse
import os
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from refresh_feed import SOURCES, ZONE, atomic_json


def validate_snapshot(feed: dict, code: str, expected_date: str | None = None) -> dict:
    if feed.get('schemaVersion') != 1 or feed.get('provinceCode') != code:
        raise ValueError('Invalid recovered envelope')
    datetime.strptime(feed['dutyDate'], '%Y-%m-%d')
    timestamp = datetime.fromisoformat(feed['fetchedAt'].replace('Z', '+00:00'))
    if timestamp.tzinfo is None or (expected_date is not None and feed['dutyDate'] != expected_date):
        raise ValueError('Invalid recovered timestamp/date')
    records = feed['pharmacies']
    if not isinstance(records, list) or not 1 <= len(records) <= 2000:
        raise ValueError('Invalid recovered records')
    if any(p.get('provinceCode') != code or not p.get('id') or not p.get('name') or not p.get('address') for p in records):
        raise ValueError('Cross-province or incomplete recovered record')
    if len({p['id'] for p in records}) != len(records):
        raise ValueError('Duplicate recovered identifiers')
    return feed


def download(url: str) -> dict:
    request = urllib.request.Request(url, headers={'User-Agent': 'NobetCep-Feed-Recovery/1.1'})
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read(3_000_001)
    if len(body) > 3_000_000:
        raise ValueError('Snapshot too large')
    return json.loads(body)


def recover(base: str, output: Path, now: datetime, loader=download) -> None:
    if not base.startswith('https://'):
        print('First deployment: PUBLIC_FEED_BASE not set; no prior snapshots to recover.')
        return
    dates = [(now.astimezone(ZONE)-timedelta(days=days)).date().isoformat() for days in (0, 1, 2)]
    for code in SOURCES:
        for filename in ['today.json'] + [f'{date}.json' for date in dates]:
            try:
                expected = None if filename == 'today.json' else filename.removesuffix('.json')
                feed = validate_snapshot(loader(f'{base.rstrip("/")}/TR-{code}/{filename}'), code, expected)
                atomic_json(output / f'TR-{code}' / filename, feed)
                print(f'Recovered {code}/{filename}; original fetch time retained.')
            except Exception:
                print(f'No valid public snapshot for {code}/{filename}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('site/data/v1'))
    args = parser.parse_args()
    recover(os.environ.get('PUBLIC_FEED_BASE', ''), args.output, datetime.now(ZONE))
