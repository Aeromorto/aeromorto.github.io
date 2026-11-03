# -*- coding: utf-8 -*-
"""Refresh assets/prices.json.

Showcase cards keep the Steam lowest listing. The inventory total is every
marketable CS2 item: those seven listings, plus a stored lowest listing for
everything else. Steam answers 429 when it is asked item by item, so a miss
keeps the previous cents. A new item with no stored listing falls back to the
latest Steam sale price from prices.csgotrader.app.
"""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "prices.json"
UA = "aeromorto-prices/1.0"
STEAMID = "76561198829218063"
SALE_URL = "https://prices.csgotrader.app/latest/steam.json"

ITEMS = [
    ("gloves", "★ Sport Gloves | Nocts (Field-Tested)"),
    ("m9", "★ M9 Bayonet"),
    ("awp", "AWP | Graphite (Factory New)"),
    ("ak", "AK-47 | Slate (Minimal Wear)"),
    ("deagle", "Desert Eagle | Hypnotic (Factory New)"),
    ("sally", "Getaway Sally | The Professionals"),
    ("ava", "Special Agent Ava | FBI"),
]
BY_NAME = {name: key for key, name in ITEMS}
CURRENCIES = (("usd", "US", "1"), ("brl", "BR", "7"))


def get_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_cents(text):
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 3:
        raise RuntimeError("unparsed price: " + text)
    return int(digits)


def lowest_cents(name, country, currency):
    qs = urllib.parse.urlencode({
        "country": country,
        "currency": currency,
        "appid": "730",
        "market_hash_name": name,
    })
    url = "https://steamcommunity.com/market/priceoverview/?" + qs
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("success") or not payload.get("lowest_price"):
        raise RuntimeError("missing lowest_price")
    return parse_cents(payload["lowest_price"])


def refresh_showcase(items):
    """Update the seven cards. Stop on the first rate limit and keep old cents."""
    for key, name in ITEMS:
        for code, country, currency in CURRENCIES:
            try:
                items[key][code] = lowest_cents(name, country, currency)
                print("showcase", key, code, items[key][code], flush=True)
            except Exception as error:
                print("showcase keep", key, code, error, flush=True)
                return
            time.sleep(4)
        time.sleep(2)


def fetch_inventory():
    qty = {}
    start = None
    pages = 0
    while True:
        url = "https://steamcommunity.com/inventory/%s/730/2?l=english&count=2000" % STEAMID
        if start:
            url += "&start_assetid=" + start
        data = get_json(url)
        pages += 1
        if not data.get("success"):
            raise RuntimeError("inventory failed")
        descriptions = {}
        for desc in data.get("descriptions") or []:
            descriptions["%s_%s" % (desc.get("classid"), desc.get("instanceid"))] = desc
        for asset in data.get("assets") or []:
            desc = descriptions.get("%s_%s" % (asset.get("classid"), asset.get("instanceid")))
            if not desc or not desc.get("marketable"):
                continue
            name = desc.get("market_hash_name")
            if not name:
                continue
            qty[name] = qty.get(name, 0) + int(asset.get("amount") or 1)
        if not data.get("more_items") or not data.get("last_assetid"):
            break
        start = str(data["last_assetid"])
        if pages > 20:
            raise RuntimeError("too many inventory pages")
    return qty


def sale_cents(row):
    if not isinstance(row, dict):
        return None
    for key in ("last_24h", "last_7d", "last_30d", "last_90d"):
        value = row.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(round(value * 100))
    return None


def recompute(items, holdings, listings):
    usd_base = sum(items[key]["usd"] for key, _name in ITEMS)
    brl_base = sum(items[key]["brl"] for key, _name in ITEMS)
    fx = brl_base / usd_base if usd_base else 0
    sales = None
    unpriced = []
    usd = 0
    brl = 0
    for name, amount in sorted(holdings.items()):
        if amount <= 0:
            continue
        key = BY_NAME.get(name)
        if key:
            unit_usd = items[key]["usd"]
            unit_brl = items[key]["brl"]
        else:
            stored = listings.get(name) or {}
            unit_usd = stored.get("usd")
            if not unit_usd:
                if sales is None:
                    try:
                        sales = get_json(SALE_URL, timeout=90)
                    except Exception as error:
                        print("sale list failed", error, flush=True)
                        sales = {}
                unit_usd = sale_cents(sales.get(name))
            if not unit_usd:
                unpriced.append(name)
                continue
            unit_brl = int(round(unit_usd * fx)) if fx else stored.get("brl") or 0
            listings[name] = {"usd": unit_usd, "brl": unit_brl}
        usd += unit_usd * amount
        brl += unit_brl * amount
    return usd, brl, round(fx, 6), unpriced


def main():
    document = json.loads(OUT.read_text(encoding="utf-8"))
    items = document["items"]
    previous = json.dumps(
        {"items": items, "inventory": document.get("inventory")},
        sort_keys=True,
    )
    inventory = document.get("inventory") or {}
    holdings = dict(inventory.get("holdings") or {})
    listings = dict(inventory.get("listings") or {})
    try:
        refresh_showcase(items)
    except Exception as error:
        print("showcase refresh stopped", error, flush=True)

    try:
        holdings = fetch_inventory()
        print("inventory units", sum(holdings.values()), "unique", len(holdings), flush=True)
    except Exception as error:
        print("inventory keep", error, flush=True)

    usd, brl, fx, unpriced = recompute(items, holdings, listings)
    document["items"] = items
    document["inventory"] = {
        "usd": usd,
        "brl": brl,
        "units": sum(amount for amount in holdings.values() if amount > 0),
        "unique": len(holdings),
        "fxBrlPerUsd": fx,
        "unpriced": unpriced,
        "holdings": holdings,
        "listings": listings,
    }
    document["source"] = (
        "Showcase cards use the Steam priceoverview lowest listing. "
        "inventory.usd and inventory.brl add every other marketable CS2 item. "
        "Stored listings stay until a new item needs a price; that fallback is "
        "the latest Steam sale on prices.csgotrader.app. Unmarketable assets are excluded."
    )
    fresh = json.dumps(
        {"items": items, "inventory": document["inventory"]},
        sort_keys=True,
    )
    if fresh == previous:
        print("unchanged", flush=True)
        return
    document["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", OUT, "usd", usd, "brl", brl, "unpriced", unpriced, flush=True)


if __name__ == "__main__":
    main()
