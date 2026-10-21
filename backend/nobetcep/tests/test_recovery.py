import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import recover_feed
import refresh_feed


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,10,21,9,tzinfo=timezone.utc)
        rows = json.loads((ROOT/'tests/fixtures/kayseri.json').read_text())
        self.feed = refresh_feed.kayseri(rows,self.now)

    def test_latest_pointer_is_recovered_without_restamping_after_partial_outage(self):
        with tempfile.TemporaryDirectory() as folder:
            def loader(url):
                if url.endswith('/TR-38/today.json'):
                    return self.feed
                raise OSError('No archive')
            recover_feed.recover('https://feed.example/v1',Path(folder),self.now,loader)
            actual = json.loads((Path(folder)/'TR-38/today.json').read_text())
            self.assertEqual(self.feed,actual)
            self.assertFalse((Path(folder)/'TR-35/today.json').exists())

    def test_recovery_rejects_wrong_province_and_duplicate_identifiers(self):
        with self.assertRaises(ValueError):
            recover_feed.validate_snapshot(self.feed,'35')
        invalid = copy.deepcopy(self.feed)
        invalid['pharmacies'].append(copy.deepcopy(invalid['pharmacies'][0]))
        with self.assertRaises(ValueError):
            recover_feed.validate_snapshot(invalid,'38')
        with self.assertRaises(ValueError):
            recover_feed.validate_snapshot(self.feed,'38','2026-10-20')
