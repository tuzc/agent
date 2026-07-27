#!/usr/bin/env python3
"""Fetch 2026 A-share historical market breadth from a single Kaipanla/LonghuVIP endpoint.

This file is used only on the temporary branch temp-market-data-20260727.
Output fields are kept raw and converted to numeric values without mixing sources.
"""
from __future__ import annotations

import csv
import json
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import requests

START = date(2026, 1, 1)
END = date(2026, 7, 24)
URL = "https://apphis.longhuvip.com/w1/api/index.php"
OUT = Path("data/market_sentiment_2026.csv")
RAW_DIR = Path("data/market_sentiment_raw")

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; SHARK PRS-A0 Build/PQ3A.190605.01141736)",
    "Accept-Encoding": "gzip",
}


def to_num(value):
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return value


def fetch_one(day: date, session: requests.Session) -> dict | None:
    day_s = day.isoformat()
    params = {"apiv": "w42", "PhoneOSNew": "1", "VerSion": "5.21.0.2"}
    payload = {
        "PhoneOSNew": "1",
        "DeviceID": str(uuid.uuid4()),
        "VerSion": "5.21.0.2",
        "apiv": "w42",
        "Day": day_s,
        "a": "HisZhangFuDetail",
        "c": "HisHomeDingPan",
    }
    for attempt in range(4):
        try:
            r = session.post(URL, params=params, data=payload, headers=HEADERS, timeout=25)
            r.raise_for_status()
            result = r.json()
            if str(result.get("errcode", "0")) not in {"0", ""}:
                return None
            info = result.get("info") or {}
            if not isinstance(info, dict) or not info:
                return None
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            (RAW_DIR / f"{day_s}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            up = to_num(info.get("SZJS"))
            down = to_num(info.get("XDJS"))
            # Non-trading days usually return no meaningful breadth counts.
            if (up in (None, 0.0)) and (down in (None, 0.0)):
                return None
            return {
                "date": day_s,
                "market_turnover_raw": to_num(info.get("qscln")),
                "sh_turnover_raw": to_num(info.get("szln")),
                "up_count": up,
                "down_count": down,
                "flat_count": to_num(info.get("0")),
                "limit_up_count": to_num(info.get("ZT")),
                "limit_down_count": to_num(info.get("DT")),
                "actual_limit_up_count": to_num(info.get("SJZT")),
                "actual_limit_down_count": to_num(info.get("SJDT")),
                "response_date": result.get("date"),
            }
        except Exception as exc:
            if attempt == 3:
                print(f"FAILED {day_s}: {type(exc).__name__}: {exc}")
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with requests.Session() as session:
        current = START
        while current <= END:
            if current.weekday() < 5:
                row = fetch_one(current, session)
                if row:
                    rows.append(row)
                    print("OK", current.isoformat(), row)
                time.sleep(0.20)
            current += timedelta(days=1)

    fieldnames = [
        "date",
        "market_turnover_raw",
        "sh_turnover_raw",
        "up_count",
        "down_count",
        "flat_count",
        "limit_up_count",
        "limit_down_count",
        "actual_limit_up_count",
        "actual_limit_down_count",
        "response_date",
    ]
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"WROTE {OUT}: {len(rows)} rows")
    if rows:
        print("FIRST", rows[0])
        print("LAST", rows[-1])


if __name__ == "__main__":
    main()
