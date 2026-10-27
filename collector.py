#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions collector (tək run, stateful DEYİL)

- etender.gov.az /api/events listini son LOOKBACK_HOURS ərzində çəkir
- hamısını ingester-ə POST edir (idempotent: ingester eventId əsasında dedupe edir)
- state yoxdur → CI üçün idealdır; cursor/lookback üst-üstə düşməsi zərərsizdir

Env:
  INGESTER_URL   — https://ingester.<subdomain>.workers.dev/ingest
  INGEST_SECRET  — X-Ingest-Secret başlığı
  LOOKBACK_HOURS — neçə saat geriyə baxılsın (default 24)
"""

import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
BASE = "https://www.etender.gov.az"
INGESTER = os.environ.get("INGESTER_URL", "").strip()
SECRET = os.environ.get("INGEST_SECRET", "").strip()
LOOKBACK = int(os.environ.get("LOOKBACK_HOURS", "24"))


def make_opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def warmup(opener):
    req = urllib.request.Request(BASE + "/", headers={"User-Agent": UA})
    with opener.open(req, timeout=25) as r:
        print(f"[etender] warmup → HTTP {r.status}")
        return r.status


def get_json(opener, path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": BASE + "/",
        "Origin": BASE,
    })
    with opener.open(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def to_query_date(dt):
    s = dt.strftime("%Y-%m-%dT%H:%M:%S")
    return s + ".000Z"


def main():
    from datetime import datetime, timedelta, timezone

    if not INGESTER or not SECRET:
        print("Xəta: INGESTER_URL və INGEST_SECRET env dəyişənləri tələb olunur")
        sys.exit(2)

    opener = make_opener()
    try:
        warmup(opener)
    except Exception as e:  # noqa: BLE001
        print(f"[etender] warmup uğursuz: {e}")
        sys.exit(1)

    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK)
    items = []
    page = 1
    try:
        while True:
            data = get_json(opener, "/api/events", {
                "EventType": "0",
                "EventStatus": "1",
                "PageSize": "100",
                "PageNumber": str(page),
                "publishDateFrom": to_query_date(since),
            })
            page_items = data.get("items", [])
            items.extend(page_items)
            total = data.get("totalItems")
            print(f"[etender] səhifə {page} · {len(page_items)} item · ümumi {total}")
            if not page_items or not data.get("hasNextPage", False) or page >= 5:
                break
            page += 1
            time.sleep(0.4)
    except urllib.error.HTTPError as e:
        print(f"[etender] HTTP {e.code}: {e.read().decode('utf-8','replace')[:300]}")
        # HTTP blok → GitHub IP-sinin etender üçün uyğun olmadığını göstərir
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"[etender] xəta: {e}")
        sys.exit(1)

    if not items:
        print("[collector] yeni tender yoxdur — OK (blok deyil, sadəcə boş)")
        return

    payload = {"source": "etender", "items": [{"list": it} for it in items]}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        INGESTER,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Ingest-Secret": SECRET},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode("utf-8"))
        print(f"[ingester] inserted={res.get('inserted')} skipped={res.get('skipped')} errors={res.get('errors')}")
    except urllib.error.HTTPError as e:
        print(f"[ingester] HTTP {e.code}: {e.read().decode('utf-8','replace')[:300]}")
        sys.exit(1)


if __name__ == "__main__":
    main()