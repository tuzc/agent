#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

DATES = [
    date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5),
    date(2026, 8, 6), date(2026, 8, 7), date(2026, 8, 10),
    date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13),
    date(2026, 8, 14),
]
OUT = Path("data/reverse_repo_update_20260814")
OUT.mkdir(parents=True, exist_ok=True)
PBC_BASE = "https://www.pbc.gov.cn"
PBC_OMO_INDEX = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html"
PBC_OUTRIGHT_INDEX = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/5492845/index.html"
KPH_URL = "https://apphis.longhuvip.com/w1/api/index.php"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
})


def request(method: str, url: str, **kwargs) -> requests.Response:
    last = None
    for attempt in range(4):
        try:
            response = session.request(method, url, timeout=35, **kwargs)
            response.raise_for_status()
            return response
        except Exception as exc:
            last = exc
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"request failed: {url}: {last}")


def soup_text(response: requests.Response) -> str:
    response.encoding = response.apparent_encoding or response.encoding
    return " ".join(BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True).split())


def pbc_7d_rows() -> list[dict]:
    response = request("GET", PBC_OMO_INDEX)
    response.encoding = response.apparent_encoding or response.encoding
    soup = BeautifulSoup(response.text, "html.parser")
    wanted = {d.isoformat() for d in DATES}
    links = {}
    for anchor in soup.find_all("a", href=True):
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if "公开市场业务交易公告" not in title or "2026" not in title:
            continue
        href = urljoin(PBC_BASE, anchor["href"])
        match = re.search(r"/(2026\d{4})\d*/index\.html", href)
        if match:
            day = datetime.strptime(match.group(1), "%Y%m%d").date().isoformat()
            if day in wanted:
                links[day] = href
    rows = []
    for day in sorted(wanted):
        if day not in links:
            raise RuntimeError(f"PBC list missing {day}; found {links}")
        text = soup_text(request("GET", links[day]))
        if "操作量为零" in text:
            amount = 0
        else:
            match = re.search(r"开展了\s*([\d,]+)\s*亿元\s*7天期逆回购操作", text)
            if not match:
                match = re.search(r"7\s*天\s*([\d,]+)\s*亿元\s*([\d,]+)\s*亿元", text)
            if not match:
                raise RuntimeError(f"cannot parse PBC amount {day}: {text[:1200]}")
            amount = int(match.group(1).replace(",", ""))
        number = re.search(r"公开市场业务交易公告\s*\[2026\]第(\d+)号", text)
        rows.append({
            "date": day,
            "seven_day_injection": amount,
            "announcement_no": int(number.group(1)) if number else None,
            "source_url": links[day],
        })
    return rows


def outright_rows() -> list[dict]:
    response = request("GET", PBC_OUTRIGHT_INDEX)
    response.encoding = response.apparent_encoding or response.encoding
    soup = BeautifulSoup(response.text, "html.parser")
    rows = []
    for anchor in soup.find_all("a", href=True):
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if "买断式逆回购" not in title or "2026" not in title:
            continue
        href = urljoin(PBC_BASE, anchor["href"])
        match = re.search(r"/(2026\d{4})\d*/index\.html", href)
        if not match:
            continue
        day = datetime.strptime(match.group(1), "%Y%m%d").date()
        if not (DATES[0] <= day <= DATES[-1]):
            continue
        text = soup_text(request("GET", href))
        amount = re.search(r"开展\s*([\d,]+)\s*亿元", text)
        term = re.search(r"期限为([^，。]+)", text)
        maturity = re.search(r"到期日为(2026|2027)年(\d+)月(\d+)日", text)
        rows.append({
            "date": day.isoformat(),
            "tool": "买断式逆回购",
            "amount": int(amount.group(1).replace(",", "")) if amount else None,
            "term": term.group(1) if term else None,
            "maturity_date": (
                f"{maturity.group(1)}-{int(maturity.group(2)):02d}-{int(maturity.group(3)):02d}"
                if maturity else None
            ),
            "source_url": href,
        })
    return sorted(rows, key=lambda x: x["date"])


def index_row(day: date) -> dict:
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    day_s = day.strftime("%Y%m%d")
    params = {
        "secid": "1.000001", "klt": "101", "fqt": "0",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "beg": day_s, "end": day_s,
    }
    response = request("GET", url, params=params)
    payload = response.json()
    klines = ((payload.get("data") or {}).get("klines") or [])
    if len(klines) != 1:
        raise RuntimeError(f"index missing {day}: {payload}")
    parts = klines[0].split(",")
    trade_date, open_px, close_px, high_px, low_px, volume, amount, amplitude, pct, change, turnover = parts[:11]
    close_value = float(close_px)
    change_value = float(change)
    return {
        "date": day.isoformat(),
        "prev_close": close_value - change_value,
        "open": float(open_px), "high": float(high_px), "low": float(low_px),
        "close": close_value, "sse_turnover_yi": float(amount) / 1e8,
        "source_url": response.url,
    }


def number(value):
    if value in (None, "", "--"):
        return None
    return float(str(value).replace(",", ""))


def sentiment_row(day: date) -> dict:
    day_s = day.isoformat()
    params = {"apiv": "w42", "PhoneOSNew": "1", "VerSion": "5.21.0.2"}
    payload = {
        "PhoneOSNew": "1", "DeviceID": str(uuid.uuid4()),
        "VerSion": "5.21.0.2", "apiv": "w42", "Day": day_s,
        "a": "HisZhangFuDetail", "c": "HisHomeDingPan",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; SHARK PRS-A0 Build/PQ3A.190605.01141736)",
        "Accept-Encoding": "gzip",
    }
    response = request("POST", KPH_URL, params=params, data=payload, headers=headers)
    result = response.json()
    info = result.get("info") or {}
    if not info:
        raise RuntimeError(f"sentiment missing {day}: {result}")
    return {
        "date": day_s,
        "market_turnover_yi": number(info.get("qscln")) / 10000,
        "up_count": int(number(info.get("SZJS"))),
        "down_count": int(number(info.get("XDJS"))),
        "flat_count": int(number(info.get("0")) or 0),
        "limit_up_count": int(number(info.get("ZT"))),
        "limit_down_count": int(number(info.get("DT"))),
        "actual_limit_up_count": int(number(info.get("SJZT")) or 0),
        "actual_limit_down_count": int(number(info.get("SJDT")) or 0),
        "response_date": result.get("date"),
        "source_url": KPH_URL,
    }


def main() -> None:
    pbc = pbc_7d_rows()
    outright = outright_rows()
    index = [index_row(day) for day in DATES]
    sentiment = [sentiment_row(day) for day in DATES]
    expected = [d.isoformat() for d in DATES]
    for name, rows in [("pbc", pbc), ("index", index), ("sentiment", sentiment)]:
        actual = [row["date"] for row in rows]
        if actual != expected:
            raise RuntimeError(f"{name} mismatch {actual} != {expected}")
    for market, breadth in zip(index, sentiment):
        if breadth["market_turnover_yi"] + 0.01 < market["sse_turnover_yi"]:
            raise RuntimeError(f"turnover mismatch {market['date']}: {market} {breadth}")
    data = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "dates": expected, "pbc_7d": pbc, "pbc_outright": outright,
        "index": index, "sentiment": sentiment,
    }
    (OUT / "update_data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for filename, rows in [
        ("pbc_7d.csv", pbc), ("pbc_outright.csv", outright),
        ("index.csv", index), ("sentiment.csv", sentiment),
    ]:
        if not rows:
            continue
        with (OUT / filename).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader(); writer.writerows(rows)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
