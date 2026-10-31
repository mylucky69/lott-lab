"""Client for The Lott public results API (the same JSON API thelott.com uses).

Every response is normalised into:
    {"game": id, "draw": int, "date": "YYYY-MM-DD", "main": [...], "supp": [...],
     "dividends": [{"div": 1, "winners": n, "amount": $}], "source": "thelott"}
"""
from __future__ import annotations

import datetime as dt
import time

import requests

from .games import Game

BASE = "https://data.api.thelott.com/sales/vmax/web/data/lotto"
COMPANY = "NSWLotteries"   # results are national; the company only affects branding
# Identify honestly as an automated tool. If The Lott refuses automated access
# (HTTP 403), we respect that and fall back to manual result entry.
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "LottLab/1.0 (personal results tracker)",
}


class FetchError(RuntimeError):
    pass


def _post(path: str, body: dict, retries: int = 3) -> dict:
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(f"{BASE}/{path}", json=body, headers=HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
            if data.get("Success") is False:
                raise FetchError(f"API error: {data.get('ErrorInfo')}")
            return data
        except (requests.RequestException, ValueError, FetchError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise FetchError(f"{path}: {last}")


def _get(obj: dict, *names, default=None):
    for n in names:
        if n in obj and obj[n] is not None:
            return obj[n]
    return default


def normalise(game: Game, raw: dict) -> dict | None:
    pid = _get(raw, "ProductId", "productId")
    if pid and pid not in game.product_ids:
        return None
    date = str(_get(raw, "DrawDate", "drawDate", default=""))[:10]
    number = _get(raw, "DrawNumber", "drawNumber")
    main = [int(n) for n in _get(raw, "PrimaryNumbers", "primaryNumbers", default=[])]
    supp = [int(n) for n in _get(raw, "SecondaryNumbers", "secondaryNumbers", default=[])]
    if not date or number is None or not main:
        return None
    divs = []
    for d in _get(raw, "Dividends", "dividends", default=[]) or []:
        divs.append({
            "div": int(_get(d, "Division", "division", default=0)),
            "winners": _get(d, "BlocNumberOfWinners", "CompanyNumberOfWinners", default=None),
            "amount": _get(d, "BlocDividend", "CompanyDividend", default=None),
        })
    return {"game": game.id, "draw": int(number), "date": date, "main": main, "supp": supp,
            "dividends": sorted(divs, key=lambda x: x["div"]), "source": "thelott"}


def _draw_list(data: dict) -> list[dict]:
    return _get(data, "DrawResults", "Draws", "drawResults", "draws", default=[]) or []


def latest(game: Game, count: int = 5) -> list[dict]:
    data = _post("latestresults", {"CompanyId": COMPANY, "MaxDrawCountPerProduct": count,
                                   "OptionalProductFilter": list(game.product_ids)})
    return [d for d in (normalise(game, r) for r in _draw_list(data)) if d]


def date_range(game: Game, start: dt.date, end: dt.date) -> list[dict]:
    data = _post("results/search/daterange", {
        "DateStart": f"{start.isoformat()}T00:00:00", "DateEnd": f"{end.isoformat()}T23:59:59",
        "ProductFilter": list(game.product_ids), "CompanyFilter": [COMPANY]})
    return [d for d in (normalise(game, r) for r in _draw_list(data)) if d]


def history(game: Game, start: dt.date, end: dt.date, chunk_days: int = 180) -> list[dict]:
    """Fetch a long period in chunks (the API limits each search)."""
    out, cur = [], start
    while cur <= end:
        stop = min(cur + dt.timedelta(days=chunk_days - 1), end)
        out.extend(date_range(game, cur, stop))
        cur = stop + dt.timedelta(days=1)
        time.sleep(0.5)
    return out


def open_draw(game: Game) -> dict | None:
    """Next open draw (number, date, jackpot) if the API offers it; None otherwise."""
    try:
        data = _post("opendraws", {"CompanyId": COMPANY, "MaxDrawCount": 1,
                                   "OptionalProductFilter": list(game.product_ids)}, retries=1)
    except FetchError:
        return None
    for r in _draw_list(data):
        if _get(r, "ProductId") not in game.product_ids:
            continue
        return {"draw": _get(r, "DrawNumber"), "date": str(_get(r, "DrawDate", default=""))[:10],
                "jackpot": _get(r, "Div1Amount", "Division1Amount")}
    return None
