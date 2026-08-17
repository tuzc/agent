#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

DATES = [
    date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5),
    date(2026, 8, 6), date(2026, 8, 7), date(2026, 8, 10),
    date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13),
    date(2026, 8, 14),
]
OUT_DIR = Path("data/reverse_repo_update_20260814")
OUT_DIR.mkdir(parents=True, exist_ok=True)

PBC_BASE = "https://www.pbc.gov.cn"
PBC_OMO_INDEX = (
    "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html"
)
PBC_OUTRIGHT_INDEX = (
    "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/5492845/index.html"
)
KPH_URL = "https://apphis.longhuvip.com/w1/api/index.php"
KPH_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; SHARK PRS-A0 Build/PQ3A.190605.01141736)",
    "Accept-Encoding": "gzip",
}

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
})


def get(url: str, *, retries: int = 4) -> requests.Response:
    last_exc = None
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=35)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or response.encoding
            return response
        except Exception as exc:
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed: {url}: {last_exc}")


def parse_pbc_7d() -> list[dict]:
    response = get(PBC_OMO_INDEX)
    soup = BeautifulSoup(response.text, "html.parser")
    wanted = {d.isoformat(): d for d in DATES}
    candidates: dict[str, str] = {}
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        if "公开市场业务交易公告" not in text or "2026" not in text:
            continue
        href = urljoin(PBC_BASE, anchor["href"])
        # Date is normally in the URL as YYYYMMDD.
        match = re.search(r"/(2026\d{4})\d+?/index\.html", href)
        if match:
            day = datetime.strptime(match.group(1), "%Y%m%d").date().isoformat()
            if day in wanted:
                candidates[day] = href

    rows = []
    for day in sorted(wanted):
        url = candidates.get(day)
        if not url:
            raise RuntimeError(f"PBC index missing target date {day}; candidates={candidates}")
        page = get(url)
        text = " ".join(BeautifulSoup(page.text, "html.parser").get_text(" ", strip=True).split())
        patterns = [
            r"开展了\s*([\d,]+)\s*亿元\s*7天期逆回购操作",
            r"7天\s*1\.40%\s*([\d,]+)亿元\s*([\d,]+)亿元",
        ]
        amount = None
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                amount = int(match.group(1).replace(",", ""))
                break
        if amount is None:
            raise RuntimeError(f"Could not parse 7d amount for {day}: {text[:1000]}")
        title_match = re.search(r"公开市场业务交易公告\s*\[2026\]第(\d+)号", text)
        rows.append({
            "date": day,
            "seven_day_injection": amount,
            "announcement_no": int(title_match.group(1)) if title_match else None,
            "source_url": url,
        })
    return rows


def parse_outright() -> list[dict]:
    response = get(PBC_OUTRIGHT_INDEX)
    soup = BeautifulSoup(response.text, "html.parser")
    rows = []
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        if "买断式逆回购" not in text or "2026" not in text:
            continue
        href = urljoin(PBC_BASE, anchor["href"])
        match = re.search(r"/(2026\d{4})\d+?/index\.html", href)
        if not match:
            continue
        day = datetime.strptime(match.group(1), "%Y%m%d").date()
        if not (DATES[0] <= day <= DATES[-1]):
            continue
        page = get(href)
        body = " ".join(BeautifulSoup(page.text, "html.parser").get_text(" ", strip=True).split())
        amount_match = re.search(r"开展\s*([\d,]+)\s*亿元.*?买断式逆回购", body)
        if not amount_match:
            amount_match = re.search(r"开展([\d,]+)亿元", body)
        term_match = re.search(r"期限为([^，。]+)", body)
        maturity_match = re.search(r"到期日为(2026|2027)年(\d+)月(\d+)日", body)
        rows.append({
            "date": day.isoformat(),
            "tool": "买断式逆回购",
            "amount": int(amount_match.group(1).replace(",", "")) if amount_match else None,
            "term": term_match.group(1) if term_match else None,
            "maturity_date": (
                f"{maturity_match.group(1)}-{int(maturity_match.group(2)):02d}-{int(maturity_match.group(3)):02d}"
                if maturity_match else None
            ),
            "source_url": href,
            "raw_text": body[:1800],
        })
    return sorted(rows, key=lambda item: item["date"])


def parse_index_snapshot(day: date) -> dict:
    file_name = f"{day.isoformat()}_收盘指数.csv"
    encoded_path = quote(f"data/raw/{file_name}", safe="/")
    url = f"https://raw.githubusercontent.com/gaohgmail/stock_monitor/main/{encoded_path}"
    response = get(url)
    reader = csv.DictReader(response.text.lstrip("\ufeff").splitlines())
    for row in reader:
        if row.get("code") == "sh000001" or row.get("name") == "上证指数":
            return {
                "date": day.isoformat(),
                "prev_close": float(row["close"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["now"]),
                "sse_turnover_yi": float(row["成交额(万)"]) / 10000,
                "source_url": (
                    "https://github.com/gaohgmail/stock_monitor/blob/main/" + encoded_path
                ),
            }
    raise RuntimeError(f"No sh000001 row for {day}: {response.text[:1000]}")


def num(value):
    if value in (None, "", "--"):
        return None
    return float(str(value).replace(",", ""))


def parse_sentiment(day: date) -> dict:
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
    last_exc = None
    for attempt in range(4):
        try:
            response = session.post(
                KPH_URL, params=params, data=payload, headers=KPH_HEADERS, timeout=30
            )
            response.raise_for_status()
            result = response.json()
            info = result.get("info") or {}
            if not info:
                raise RuntimeError(f"Empty info: {result}")
            return {
                "date": day_s,
                "market_turnover_yi": num(info.get("qscln")) / 10000,
                "up_count": int(num(info.get("SZJS"))),
                "down_count": int(num(info.get("XDJS"))),
                "flat_count": int(num(info.get("0")) or 0),
                "limit_up_count": int(num(info.get("ZT"))),
                "limit_down_count": int(num(info.get("DT"))),
                "actual_limit_up_count": int(num(info.get("SJZT")) or 0),
                "actual_limit_down_count": int(num(info.get("SJDT")) or 0),
                "response_date": result.get("date"),
                "source_url": KPH_URL,
            }
        except Exception as exc:
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Sentiment failed {day_s}: {last_exc}")


def main() -> None:
    pbc_rows = parse_pbc_7d()
    outright_rows = parse_outright()
    index_rows = [parse_index_snapshot(day) for day in DATES]
    sentiment_rows = [parse_sentiment(day) for day in DATES]

    # Cross checks.
    expected_dates = [d.isoformat() for d in DATES]
    for name, rows in [
        ("pbc", pbc_rows), ("index", index_rows), ("sentiment", sentiment_rows)
    ]:
        actual = [row["date"] for row in rows]
        if actual != expected_dates:
            raise RuntimeError(f"{name} date mismatch: {actual} != {expected_dates}")
    for idx, sentiment in zip(index_rows, sentiment_rows):
        if sentiment["market_turnover_yi"] < idx["sse_turnover_yi"]:
            raise RuntimeError(f"Total turnover below SSE on {idx['date']}")

    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "dates": expected_dates,
        "pbc_7d": pbc_rows,
        "pbc_outright": outright_rows,
        "index": index_rows,
        "sentiment": sentiment_rows,
    }
    (OUT_DIR / "update_data.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for filename, rows in [
        ("pbc_7d.csv", pbc_rows),
        ("index.csv", index_rows),
        ("sentiment.csv", sentiment_rows),
        ("pbc_outright.csv", outright_rows),
    ]:
        if not rows:
            continue
        with (OUT_DIR / filename).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
