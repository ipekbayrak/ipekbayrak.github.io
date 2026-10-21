import copy
import io
from unittest.mock import patch
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("refresh_feed", ROOT / "tools/refresh_feed.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class FeedTests(unittest.TestCase):
    def setUp(self):
        self.rows = json.loads((ROOT / "tests/fixtures/izmir.json").read_text())
        self.now = datetime(2026, 10, 21, 9, tzinfo=timezone.utc)

    def test_transient_timeout_retries_without_changing_source_records(self):
        body = json.dumps(self.rows).encode()
        with patch.object(module.urllib.request, 'urlopen', side_effect=[TimeoutError(), io.BytesIO(body)]) as request, patch.object(module.time, 'sleep'):
            self.assertEqual(self.rows, module.fetch("35"))
            self.assertEqual(2, request.call_count)

    def test_real_response_maps_coordinates_and_notes(self):
        feed = module.izmir(self.rows, self.now)
        self.assertEqual("35", feed["provinceCode"])
        self.assertEqual("2026-10-21", feed["dutyDate"])
        self.assertEqual(len(self.rows), len(feed["pharmacies"]))
        p = feed["pharmacies"][0]
        self.assertAlmostEqual(float(self.rows[0]["LokasyonX"]), p["latitude"])
        self.assertTrue(p["note"])
        self.assertTrue(p["phone"].startswith("+90"))

    def test_ids_are_unique_despite_negative_source_ids(self):
        records = module.izmir(self.rows, self.now)["pharmacies"]
        self.assertEqual(len(records), len({r["id"] for r in records}))

    def test_invalid_coordinates_preserve_record(self):
        self.rows[0]["LokasyonX"] = "NaN"
        p = module.izmir(self.rows, self.now)["pharmacies"][0]
        self.assertIsNone(p["latitude"])
        self.assertIsNotNone(p["phone"])

    def test_invalid_and_stale_dates_fail(self):
        for rows in ([], [{"Adi": "Missing date"}]):
            with self.assertRaises((ValueError, KeyError)):
                module.izmir(rows, self.now)
        with self.assertRaises(ValueError):
            module.izmir(self.rows, datetime(2026, 10, 25, 9, tzinfo=timezone.utc))

    def test_night_rollover_does_not_label_future_list_as_previous_night(self):
        future = module.izmir(self.rows, datetime(2026, 10, 20, 23, tzinfo=timezone.utc))
        self.assertEqual("2026-10-21", future["dutyDate"])

    def test_anomaly_keeps_last_good_file_byte_identical(self):
        feed = module.izmir(self.rows, self.now)
        # Simulate a healthy large feed before a drastic source failure.
        good = copy.deepcopy(feed)
        good["pharmacies"] *= 10
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            module.publish(output, good)
            target = output / "TR-35/today.json"
            previous = target.read_bytes()
            with self.assertRaises(ValueError):
                module.publish(output, feed)
            self.assertEqual(previous, target.read_bytes())

    def test_ascii_search_matches_android_normalization(self):
        self.assertEqual("izmir cigli", module.search_key("İZMİR ÇİĞLİ"))
        self.assertEqual("Ilıca", module.title("ILICA"))

    def test_kayseri_real_response_zero_ids_dates_hours_and_phones(self):
        rows = json.loads((ROOT / "tests/fixtures/kayseri.json").read_text())
        feed = module.kayseri(rows, self.now)
        self.assertEqual("38", feed["provinceCode"])
        self.assertEqual("2026-10-21", feed["dutyDate"])
        self.assertEqual(14, len(feed["pharmacies"]))
        self.assertEqual(14, len({p["id"] for p in feed["pharmacies"]}))
        self.assertEqual("+903522223634", feed["pharmacies"][0]["phone"])
        self.assertTrue(any("01.00" in p["note"] for p in feed["pharmacies"]))

    def test_kayseri_does_not_silently_repair_impossible_dates(self):
        rows = json.loads((ROOT / "tests/fixtures/kayseri.json").read_text())
        rows[0]["watchDate"] = "31.02.2026"
        with self.assertRaises(ValueError):
            module.kayseri(rows, self.now)

    def test_kayseri_bad_coordinate_retains_record(self):
        rows = json.loads((ROOT / "tests/fixtures/kayseri.json").read_text())
        rows[0]["latitude"] = 0
        records = module.kayseri(rows, self.now)["pharmacies"]
        self.assertEqual(14, len(records))
        self.assertIsNone(records[0]["latitude"])
        self.assertIsNotNone(records[0]["phone"])

    def test_one_provider_failure_preserves_its_data_and_updates_healthy_city(self):
        kayseri = json.loads((ROOT / "tests/fixtures/kayseri.json").read_text())
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            module.publish(output, module.izmir(self.rows, self.now))
            old = (output / "TR-35/today.json").read_bytes()
            def load(code):
                if code == "35":
                    raise OSError("Simulated outage")
                return kayseri
            status = module.refresh_sources(output, ["35", "38"], self.now, load)
            self.assertEqual(old, (output / "TR-35/today.json").read_bytes())
            self.assertEqual("38", json.loads((output / "TR-38/today.json").read_text())["provinceCode"])
            self.assertEqual(["error", "ok"], [p["status"] for p in status["providers"]])

    def test_cross_province_envelope_cannot_replace_valid_cache(self):
        feed = module.izmir(self.rows, self.now)
        feed["provinceCode"] = "38"
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                module.publish(Path(temp), feed)

if __name__ == "__main__":
    unittest.main()
