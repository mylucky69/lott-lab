"""Public results pages used as data sources.

Rules we follow for every site:
  * honest User-Agent that names this tool,
  * robots.txt is checked before any page is downloaded,
  * a pause between requests, and pages are only fetched when a new result is due.

Sources
  national-lottery  australia.national-lottery.com  full history, one page per year
  lotteryextreme    www.lotteryextreme.com            last 20 draws + dividends (cross-check)
"""
from __future__ import annotations

import datetime as dt
import html
import re
import time
import urllib.robotparser
from urllib.parse import urlparse

import requests

from .games import Game

UA = "LottLab/1.0 (personal results tracker; +https://github.com/mylucky69/lott-lab)"
PAUSE = 3.0
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}
_last_request = 0.0


class SourceError(RuntimeError):
    pass


def polite_get(url: str) -> str:
    global _last_request
    host = urlparse(url).netloc
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(f"https://{host}/robots.txt", headers={"User-Agent": UA}, timeout=20)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except requests.RequestException:
            rp.parse([])
        _robots[host] = rp
    if not _robots[host].can_fetch(UA, url):
        raise SourceError(f"robots.txt does not allow {url}")
    wait = PAUSE - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    except requests.RequestException as e:
        raise SourceError(f"{url}: {e}") from e
    if r.status_code != 200:
        raise SourceError(f"{url}: HTTP {r.status_code}")
    return r.text


def _clean(s: str) -> str:
    return re.sub(r"<script.*?</script>|<style.*?</style>|<ins.*?</ins>", "", s, flags=re.S | re.I)


# -- australia.national-lottery.com ----------------------------------------------
NL_SLUG = {"oz": "oz-lotto", "powerball": "powerball", "saturday": "saturday-lotto"}
NL_BASE = "https://australia.national-lottery.com"


def nl_year_url(game: Game, year: int) -> str:
    return f"{NL_BASE}/{NL_SLUG[game.id]}/results-archive-{year}"


def parse_national_lottery(game: Game, page: str) -> list[dict]:
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", _clean(page), flags=re.S):
        m = re.search(r"Draw\s*(\d+)", row)
        d = re.search(r"/results/(\d{2})-(\d{2})-(\d{4})", row)
        if not (m and d):
            continue
        main, supp = [], []
        for cls, val in re.findall(r'<li[^>]*class="([^"]*)"[^>]*>\s*(\d+)\s*</li>', row):
            tokens = cls.lower().split()
            if "ball" not in " ".join(tokens):
                continue
            (main if tokens[-1] == "ball" else supp).append(int(val))
        if game.pb_pool and len(main) == game.drawn + 1 and not supp:
            supp = [main.pop()]  # Powerball listed last without its own class
        out.append({"game": game.id, "draw": int(m.group(1)),
                    "date": f"{d.group(3)}-{d.group(2)}-{d.group(1)}",
                    "main": sorted(main), "supp": supp if game.pb_pool else sorted(supp),
                    "dividends": [], "source": "national-lottery"})
    return out


def national_lottery_year(game: Game, year: int) -> list[dict]:
    return parse_national_lottery(game, polite_get(nl_year_url(game, year)))


# -- www.lotteryextreme.com -------------------------------------------------------
LE_SLUG = {"oz": "ozlotto", "powerball": "powerball", "saturday": "saturdaylotto"}
LE_BASE = "https://www.lotteryextreme.com/australia"


def parse_lotteryextreme(game: Game, page: str) -> list[dict]:
    out = []
    pat = re.compile(r"\((\d{2})/(\d{2})/(\d{4})\)\s*-\s*Draw\s*(\d+).*?<ul class='displayball'[^>]*>(.*?)</ul>", re.S)
    for dd, mm, yyyy, num, balls in pat.findall(_clean(page)):
        before, _, after = balls.partition('class="dbx"')
        main = [int(x) for x in re.findall(r"<li>\s*(\d+)", before)]
        supp = [int(x) for x in re.findall(r"<li>\s*(\d+)", after)]
        out.append({"game": game.id, "draw": int(num), "date": f"{yyyy}-{mm}-{dd}",
                    "main": sorted(main), "supp": supp if game.pb_pool else sorted(supp),
                    "dividends": [], "source": "lotteryextreme"})
    return out


def lotteryextreme_latest(game: Game) -> list[dict]:
    return parse_lotteryextreme(game, polite_get(f"{LE_BASE}/{LE_SLUG[game.id]}-results"))


def parse_le_dividends(page: str) -> list[dict]:
    """Rows: <th>Div. 1<br>Match 7<td>0<td>$0.00<td>$0.00  (winners, payout per winner, pool)."""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", _clean(page), flags=re.S | re.I):
        cells = [html.unescape(re.sub(r"<[^>]+>", " ", c)).replace("\xa0", " ").strip()
                 for c in re.split(r"<t[dh][^>]*>", row, flags=re.I)[1:]]
        if len(cells) < 3:
            continue
        m = re.match(r"Div(?:ision|\.)?\s*(\d+)", cells[0], re.I)
        if not m:
            continue
        def number(c):
            v = re.sub(r"[^\d.]", "", c)
            return float(v) if v else None
        winners, amount = number(cells[1]), number(cells[2])
        out.append({"div": int(m.group(1)), "winners": int(winners) if winners is not None else None,
                    "amount": amount})
    return out


def lotteryextreme_dividends(game: Game, date: str) -> list[dict]:
    return parse_le_dividends(polite_get(f"{LE_BASE}/{LE_SLUG[game.id]}-dividends({date})"))


def parse_le_draw_page(game: Game, page: str) -> tuple[dict | None, str | None]:
    """One dividends page = one draw (numbers + prizes) and a link to the previous draw."""
    t = _clean(page)
    h = re.search(r"<h1>[^<]*?Results\s+(\d+)\s*-\s*(\d{2})/(\d{2})/(\d{4})", t)
    balls = re.search(r"<ul class='displayball'[^>]*>(.*?)</ul>", t, re.S)
    prev = re.search(r"href='" + LE_SLUG[game.id] + r"-dividends\((\d{4}-\d{2}-\d{2})\)'", t)
    draw = None
    if h and balls:
        before, _, after = balls.group(1).partition('class="dbx"')
        main = [int(x) for x in re.findall(r"<li>\s*(\d+)", before)]
        supp = [int(x) for x in re.findall(r"<li>\s*(\d+)", after)]
        draw = {"game": game.id, "draw": int(h.group(1)), "date": f"{h.group(4)}-{h.group(3)}-{h.group(2)}",
                "main": sorted(main), "supp": supp if game.pb_pool else sorted(supp),
                "dividends": parse_le_dividends(page), "source": "lotteryextreme"}
    prev_date = prev.group(1) if prev else None
    if draw and prev_date and prev_date >= draw["date"]:
        prev_date = None
    return draw, prev_date


def lotteryextreme_draw(game: Game, date: str) -> tuple[dict | None, str | None]:
    return parse_le_draw_page(game, polite_get(f"{LE_BASE}/{LE_SLUG[game.id]}-dividends({date})"))


# -- lotteryguru.com: next jackpot ----------------------------------------------------
LG_SLUG = {"oz": "au-oz-lotto", "powerball": "au-powerball", "saturday": "au-saturday-lotto"}
LG_INDEX = "https://lotteryguru.com/australia-lottery-results"


def parse_lotteryguru_jackpots(page: str) -> dict[str, dict]:
    """Each game card has 'Next draw' date, 'Next Jackpot' amount and a link id draw_<slug>."""
    out = {}
    for m in re.finditer(r'id="draw_(au-[a-z0-9-]+)"', page):
        block = page[max(0, m.start() - 3000):m.start()]
        jp = re.findall(r'Next Jackpot</div>\s*<div class="lg-sum">\s*A?\$\s*([\d,]+)', block)
        text = re.sub(r"<[^>]+>", " ", block)
        nd = re.findall(r"Next draw\s+\w+day\s+(\d{1,2}\s+[A-Za-z]{3})\s+(\d{4})", re.sub(r"\s+", " ", text))
        nd = [f"{a} {b}" for a, b in nd]
        if jp:
            date = None
            if nd:
                try:
                    date = dt.datetime.strptime(nd[-1], "%d %b %Y").date().isoformat()
                except ValueError:
                    pass
            out.setdefault(m.group(1), {"jackpot": float(jp[-1].replace(",", "")), "date": date})
    return out


def next_jackpot(game: Game) -> dict | None:
    """{'jackpot': 60000000.0, 'date': '2026-11-03'} or None."""
    slug = LG_SLUG[game.id]
    for url in (LG_INDEX, f"{LG_INDEX}/{slug}/{slug}-results-history"):
        try:
            found = parse_lotteryguru_jackpots(polite_get(url)).get(slug)
        except SourceError:
            continue
        if found:
            return found
    return None


def lg_history_url(game: Game, page: int = 1) -> str:
    slug = LG_SLUG[game.id]
    url = f"{LG_INDEX}/{slug}/{slug}-results-history"
    return url if page <= 1 else f"{url}?page={page}"


def parse_lotteryguru_history(page: str) -> dict:
    """Jackpot of each draw on a history page, plus paging info and the next jackpot."""
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", _clean(page))))
    draws = []
    pat = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) (\d{1,2} [A-Z][a-z]{2} \d{4})(?: \d{1,2})+ Jackpot A\$ ?([\d,]+)"
    for date, amount in re.findall(pat, text):
        try:
            iso = dt.datetime.strptime(date, "%d %b %Y").date().isoformat()
        except ValueError:
            continue
        draws.append({"date": iso, "jackpot": float(amount.replace(",", ""))})
    last = re.search(r'lastPage="(\d+)"', page)
    nxt = re.search(r"Next Jackpot A\$ ?([\d,]+)", text)
    nd = re.search(r"Next draw \w+day (\d{1,2} [A-Z][a-z]{2}) (\d{4})", text)
    next_date = None
    if nd:
        try:
            next_date = dt.datetime.strptime(f"{nd.group(1)} {nd.group(2)}", "%d %b %Y").date().isoformat()
        except ValueError:
            pass
    return {"draws": draws, "last_page": int(last.group(1)) if last else 1,
            "next": {"jackpot": float(nxt.group(1).replace(",", "")), "date": next_date} if nxt else None}


def lotteryguru_history(game: Game, page: int = 1) -> dict:
    return parse_lotteryguru_history(polite_get(lg_history_url(game, page)))
