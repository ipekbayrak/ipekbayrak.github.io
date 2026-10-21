#!/usr/bin/env python3
"""Licensed official data -> small static feed. Python 3.11+, no packages or keys."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ENDPOINT = "https://openapi.izmir.bel.tr/api/ibb/nobetcieczaneler"
DATASET = "https://acikveri.bizizmir.com/tr/dataset/nobetci-eczaneler-ve-eczane-listesi"
LICENSE = "https://creativecommons.org/licenses/by/4.0/deed.tr"
ZONE = ZoneInfo("Europe/Istanbul")
SOURCES = {
    "35": {"name": "İzmir", "endpoint": ENDPOINT, "dataset": DATASET},
    "38": {"name": "Kayseri", "endpoint": "https://acikveri.kayseri.bel.tr/api/kbb/eczane",
           "dataset": "https://acikveri.kayseri.bel.tr/veri-seti/eczane-bilgileri/11"},
}

def search_key(text: str) -> str:
    text = text.replace("I", "ı").replace("İ", "i").lower().replace("ı", "i")
    return " ".join("".join(c for c in unicodedata.normalize("NFD", text)
                            if unicodedata.category(c) != "Mn").split())

def title(text: str) -> str:
    def word_title(word):
        word = word.replace("I", "ı").replace("İ", "i").lower()
        first = {"i": "İ", "ı": "I"}.get(word[0], word[0].upper())
        return first + word[1:]
    return " ".join(word_title(w) for w in text.split())

def phone(value) -> str | None:
    number = re.sub(r"\D", "", str(value or ""))
    if len(number) == 12 and number.startswith("90"):
        return "+" + number
    if len(number) == 11 and number.startswith("0"):
        return "+90" + number[1:]
    if len(number) == 10 and number[0] in "2345":
        return "+90" + number
    return None

def izmir(rows: list, now: datetime) -> dict:
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2000:
        raise ValueError("Expected a non-empty pharmacy array")
    dates, records = set(), {}
    for row in rows:
        date = datetime.fromisoformat(str(row["Tarih"])).date()
        dates.add(date)
        name, region, address = (str(row.get(key) or "").strip() for key in ("Adi", "Bolge", "Adres"))
        if not name or not address:
            raise ValueError("Missing name/address")
        try:
            lat, lon = (float(str(row[key]).replace(",", ".")) for key in ("LokasyonX", "LokasyonY"))
            if not (37 <= lat <= 40 and 25.5 <= lon <= 29):
                lat = lon = None
        except (ValueError, TypeError, KeyError):
            lat = lon = None
        identity = search_key(f"{name}|{region}|{address}")
        record_id = "izmir-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        records[record_id] = {
            "id": record_id, "name": title(name), "provinceCode": "35",
            "region": title(region or "Bölge belirtilmemiş"), "address": title(address),
            "phone": phone(row.get("Telefon")), "latitude": lat, "longitude": lon,
            "note": str(row.get("BolgeAciklama") or "").strip(),
        }
    if len(dates) != 1:
        raise ValueError("Mixed duty dates; refusing to publish")
    date = next(iter(dates))
    local = now.astimezone(ZONE)
    expected = (local - timedelta(hours=8)).date()
    if not expected <= date <= local.date():
        raise ValueError(f"Upstream date {date} is outside the expected night window {expected}..{local.date()}")
    return {
        "schemaVersion": 1, "provinceCode": "35", "dutyDate": date.isoformat(),
        "fetchedAt": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sourceName": "İzmir Büyükşehir Belediyesi Açık Veri", "sourceUrl": DATASET,
        "licenseUrl": LICENSE, "sourceUpdatedAt": None,
        "pharmacies": list(records.values()),
    }

def kayseri(rows: list, now: datetime) -> dict:
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2000:
        raise ValueError("Expected a non-empty pharmacy array")
    dates, records = set(), {}
    for row in rows:
        raw_date = str(row["watchDate"])
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", raw_date):
            raise ValueError("Unexpected Kayseri date format")
        date = datetime.strptime(raw_date, "%d.%m.%Y").date()
        dates.add(date)
        name, region, address = (str(row.get(key) or "").strip() for key in ("name", "district", "address"))
        if not name or not address:
            raise ValueError("Missing name/address")
        try:
            lat, lon = (float(str(row[key]).replace(",", ".")) for key in ("latitude", "longitude"))
            if not (37.5 <= lat <= 40 and 34 <= lon <= 37):
                lat = lon = None
        except (ValueError, TypeError, KeyError):
            lat = lon = None
        record_id = "kayseri-" + hashlib.sha256(search_key(f"{name}|{region}|{address}").encode()).hexdigest()[:24]
        records[record_id] = {
            "id": record_id, "name": title(name), "provinceCode": "38",
            "region": title(region or "Bölge belirtilmemiş"), "address": title(address),
            "phone": phone(row.get("phone")), "latitude": lat, "longitude": lon,
            "note": " · ".join(str(row.get(key) or "").strip() for key in ("watchType", "message") if str(row.get(key) or "").strip()),
        }
    if len(dates) != 1:
        raise ValueError("Mixed duty dates; refusing to publish")
    date = next(iter(dates))
    local = now.astimezone(ZONE)
    expected = (local - timedelta(hours=8)).date()
    if not expected <= date <= local.date():
        raise ValueError("Upstream date is outside the expected night window")
    return {"schemaVersion": 1, "provinceCode": "38", "dutyDate": date.isoformat(),
            "fetchedAt": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "sourceName": "Kayseri Büyükşehir Belediyesi Açık Veri", "sourceUrl": SOURCES["38"]["dataset"],
            "licenseUrl": LICENSE, "sourceUpdatedAt": None, "pharmacies": list(records.values())}

def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".feed-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def fetch(code: str = "35") -> list:
    request = urllib.request.Request(SOURCES[code]["endpoint"], headers={"User-Agent": "NobetCep-Feed/1.0 (licensed open data)", "Accept": "application/json"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                body = response.read(3_000_001)
                if len(body) > 3_000_000:
                    raise ValueError("Unexpected response size")
                return json.loads(body)
        except (urllib.error.URLError, TimeoutError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code < 500 and error.code != 429:
                raise
            if attempt == 1:
                raise
            print(f"{code}: transient network error; retrying once", file=sys.stderr, flush=True)
            time.sleep(2)

def publish(output: Path, feed: dict) -> None:
    code = feed["provinceCode"]
    if code not in SOURCES or any(p["provinceCode"] != code for p in feed["pharmacies"]):
        raise ValueError("Invalid province envelope")
    target = output / f"TR-{code}"
    previous = target / "today.json"
    if previous.exists():
        old = json.loads(previous.read_text())
        old_count, new_count = len(old["pharmacies"]), len(feed["pharmacies"])
        if old_count >= 20 and new_count < old_count * .4:
            raise ValueError(f"Anomaly: record count fell from {old_count} to {new_count}")
    # Write dated archive before updating the latest pointer; never restamp old data.
    atomic_json(target / f'{feed["dutyDate"]}.json', feed)
    atomic_json(previous, feed)
    for archive in sorted(target.glob("20??-??-??.json"), reverse=True)[3:]:
        archive.unlink()
    atomic_json(target / "status.json", {"provinceCode": code, "status": "ok", "fetchedAt": feed["fetchedAt"], "dutyDate": feed["dutyDate"],
                                         "recordCount": len(feed["pharmacies"]), "withCoordinates": sum(p["latitude"] is not None for p in feed["pharmacies"])})

def refresh_sources(output: Path, codes: list[str], now: datetime, loader=fetch) -> dict:
    statuses = []
    for code in codes:
        try:
            feed = {"35": izmir, "38": kayseri}[code](loader(code), now)
            publish(output, feed)
            statuses.append({"provinceCode": code, "status": "ok", "fetchedAt": feed["fetchedAt"], "recordCount": len(feed["pharmacies"])})
            print(f'{SOURCES[code]["name"]}: {len(feed["pharmacies"])} records, source date {feed["dutyDate"]}; published atomically.', flush=True)
        except Exception as error:
            statuses.append({"provinceCode": code, "status": "error", "error": str(error)})
            print(f"{code}: refresh failed; previous feed preserved: {error}", file=sys.stderr, flush=True)
    result = {"schemaVersion": 1, "checkedAt": now.isoformat(), "providers": statuses}
    atomic_json(output / "status.json", result)
    atomic_json(output / "coverage.json", {"schemaVersion": 1, "provinces": [
        {"code": code, "name": source["name"], "source": source["dataset"],
         "status": "integrated" if (output / f"TR-{code}/today.json").exists() else "unavailable"}
        for code, source in SOURCES.items()], "otherProvinces": "official-link-only"})
    return result

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("site/data/v1"))
    parser.add_argument("--fixture", type=Path, help="Contract testing only; fixture date must still be current")
    parser.add_argument("--province", choices=list(SOURCES), action="append")
    parser.add_argument("--allow-partial", action="store_true", help="Deploy healthy provinces while retaining failed provinces' last-good data")
    args = parser.parse_args()
    try:
        codes = args.province or (["35"] if args.fixture else list(SOURCES))
        if args.fixture and len(codes) != 1:
            raise ValueError("A fixture must target exactly one province")
        loader = (lambda code: json.loads(args.fixture.read_text())) if args.fixture else fetch
        result = refresh_sources(args.output, codes, datetime.now(timezone.utc), loader)
        successes = sum(p["status"] == "ok" for p in result["providers"])
        return 0 if successes == len(codes) or (args.allow_partial and successes > 0) else 1
    except Exception as error:
        print(f"Feed refresh failed; previous feed preserved: {error}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
