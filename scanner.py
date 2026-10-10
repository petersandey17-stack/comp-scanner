#!/usr/bin/env python3
"""Instant-win break-even scanner (Stealth + RaffleX sites like Kilted)."""
import os
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
CREDIT_WEIGHT = float(os.environ.get("CREDIT_WEIGHT", "0.5"))
TICKET_WEIGHT = float(os.environ.get("TICKET_WEIGHT", "0.5"))
MARGIN = float(os.environ.get("MARGIN", "1.0"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "120"))
UA = {"User-Agent": "Mozilla/5.0"}

# Prizes with no £ amount in the name. Values are ESTIMATES: edit them.
NAMED_VALUES = {
    "VW GOLF R": 18000,
    "AUDI Q2": 12000,
    "VW T-ROC": 10000,
    "ROLEX ZOMBIE": 12000,
    "ROLEX BATMAN": 12000,
}


def num(s):
    return int(s.replace(",", ""))


def clean(s):
    return re.sub(r"[^\w£.,&\- ]", "", s).strip().upper()


def prize_value(title):
    t = clean(title)
    for name, v in NAMED_VALUES.items():
        if name in t:
            return float(v)
    if "TICKET" in t:  # entries into another draw: only count '£30 ticket bundle'
        m = re.search(r"£\s*([\d,]+(?:\.\d+)?)\s*TICKET BUNDLE", t)
        return float(m.group(1).replace(",", "")) * TICKET_WEIGHT if m else None
    m = re.search(r"£\s*([\d,]+(?:\.\d+)?)", t)
    if m:
        v = float(m.group(1).replace(",", ""))
        return v * CREDIT_WEIGHT if "CREDIT" in t else v
    return None


def analyse(text):
    price = None
    m = re.search(r"(\d+)p\s*Per Ticket", text, re.I)
    if m:
        price = int(m.group(1)) / 100
    else:
        m = re.search(r"£(\d+(?:\.\d+)?)\s*Per Ticket", text, re.I)
        if m:
            price = float(m.group(1))
        else:  # Kilted style: a line that is just the price
            m = re.search(r"^£(\d+(?:\.\d+)?)$", text, re.M)
            if m:
                price = float(m.group(1))

    total = None
    m = re.search(r"Max Tickets:\s*([\d,]+)", text)
    if m:
        total = num(m.group(1))
    m = re.search(r"Sold\s*([\d,]+)\s*/\s*([\d.,]+)(k?)(?![A-Za-z])", text, re.I)
    sold = num(m.group(1)) if m else None
    if total is None and m:
        total = int(float(m.group(2).replace(",", "")) * (1000 if m.group(3) else 1))

    if None in (price, total, sold):
        raise ValueError("couldn't find price / sold / max tickets on the page")

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    prizes, unknown, value_left = [], [], 0.0
    for i, line in enumerate(lines):
        m = re.fullmatch(r"([\d,]+)/([\d,]+) (Remain|Found)", line)
        if not m or i == 0:
            continue
        a, b = num(m.group(1)), num(m.group(2))
        left = a if m.group(3) == "Remain" else b - a  # Kilted says "Found"
        title = lines[i - 1]
        v = prize_value(title)
        if v is None:
            unknown.append(clean(title))
            continue
        prizes.append((clean(title), left, v))
        value_left += left * v

    tickets_left = total - sold
    ev = value_left / tickets_left if tickets_left > 0 else 0
    return dict(price=price, total=total, sold=sold, tickets_left=tickets_left,
                value_left=value_left, ev=ev, prizes=prizes, unknown=unknown)


def notify(msg):
    if not NTFY_TOPIC:
        print("(no NTFY_TOPIC set, not sending)")
        return
    requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode("utf-8"),
                  headers={"Title": "Instant win break-even!"}, timeout=15)


def fetch(url):
    r = requests.get(url, timeout=20, headers=UA)
    r.raise_for_status()
    return r.text


def norm(u):
    return u.split("#")[0].split("?")[0].rstrip("/")


def find_links(html, base):
    out = set()
    host = urlparse(base).netloc
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        u = norm(urljoin(base, a["href"]))
        if urlparse(u).netloc == host and "/competition/" in u:
            out.add(u)
    return out


def main():
    entries = [l.strip() for l in open("sites.txt") if l.strip() and not l.startswith("#")]
    queue = []
    for e in entries:
        if "/competition/" in e:
            queue.append(norm(e))
            continue
        try:
            links = find_links(fetch(e), e)
        except Exception as ex:
            print(f"{e}\n  ERROR: {ex}")
            continue
        if not links:
            print(f"{e}\n  WARNING: found no competition links (page may load them with JavaScript)")
        queue += sorted(links)

    seen, results = set(), []
    while queue and len(seen) < MAX_PAGES:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        time.sleep(1)
        try:
            html = fetch(url)
        except Exception as ex:
            print(f"{url}\n  ERROR: {ex}")
            continue
        for l in sorted(find_links(html, url)):
            if l not in seen and l not in queue:
                queue.append(l)
        text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
        try:
            a = analyse(text)
        except ValueError:
            continue
        if not a["prizes"] or a["tickets_left"] <= 0:
            continue
        results.append((url, a))

    results.sort(key=lambda r: r[1]["ev"] / r[1]["price"], reverse=True)
    print(f"Scanned {len(seen)} pages, {len(results)} instant-win competitions")
    for url, a in results:
        flag = a["ev"] > a["price"] * MARGIN
        name = url.rsplit("/", 1)[-1]
        print(f"{'** ' if flag else '   '}{name}: worth {a['ev']*100:.0f}p vs {a['price']*100:.0f}p price, "
              f"{a['tickets_left']:,} tickets left")
        if flag:
            notify(f"{url}\n{a['ev']*100:.0f}p value per ticket vs {a['price']*100:.0f}p price. "
                   f"{a['tickets_left']:,} tickets left.")


if __name__ == "__main__":
    main()
