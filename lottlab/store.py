"""JSON file storage and draw schedule (all inside the git repo, so GitHub keeps history)."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from .games import Game, valid_draw

ROOT = Path(os.environ.get("LOTT_ROOT", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"
SITE_DATA = ROOT / "docs" / "data"
SYDNEY = ZoneInfo("Australia/Sydney")
LOCK_HOUR = int(os.environ.get("LOTT_LOCK_HOUR", "17"))  # lock at 17:00 Sydney, the day before the draw


def now_sydney() -> dt.datetime:
    fake = os.environ.get("LOTT_NOW")  # for testing: "2026-11-02T18:00"
    if fake:
        return dt.datetime.fromisoformat(fake).replace(tzinfo=SYDNEY)
    return dt.datetime.now(SYDNEY)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, obj) -> bool:
    """Write only if content changed (keeps git history clean). Returns True if written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


# -- draws ------------------------------------------------------------------
def draws_path(game: Game) -> Path:
    return DATA / game.id / "draws.json"


def load_draws(game: Game, valid_only: bool = True) -> list[dict]:
    draws = read_json(draws_path(game), [])
    if valid_only:
        draws = [d for d in draws if valid_draw(game, d)]
    return sorted(draws, key=lambda d: d["draw"])


def merge_draws(game: Game, new: list[dict]) -> tuple[list[int], list[str]]:
    """Add draws from a source. Same draw from a second source with identical numbers
    marks it verified; different numbers are reported as a conflict (existing data kept)."""
    current = {d["draw"]: d for d in read_json(draws_path(game), [])}
    added, conflicts = [], []
    for d in new:
        src = d.get("source", "?")
        cur = current.get(d["draw"])
        if cur is None:
            d = {**d, "sources": [src]}
            d.pop("source", None)
            current[d["draw"]] = d
            added.append(d["draw"])
            continue
        if cur["main"] == d["main"] and cur["supp"] == d["supp"]:
            if src not in cur.setdefault("sources", []):
                cur["sources"].append(src)
            if d.get("dividends") and not cur.get("dividends"):
                cur["dividends"] = d["dividends"]
        else:
            conflicts.append(f"#{d['draw']}: {src} {d['main']}+{d['supp']} != {cur['main']}+{cur['supp']}")
    write_json(draws_path(game), sorted(current.values(), key=lambda d: d["draw"]))
    return sorted(added), conflicts


def set_dividends(game: Game, draw: int, dividends: list[dict]) -> bool:
    current = read_json(draws_path(game), [])
    for d in current:
        if d["draw"] == draw and dividends:
            d["dividends"] = dividends
            return write_json(draws_path(game), current)
    return False


def set_jackpots(game: Game, items: list[dict]) -> int:
    """Attach the advertised division-1 jackpot to stored draws, matched by date."""
    by_date = {i["date"]: i["jackpot"] for i in items}
    current = read_json(draws_path(game), [])
    n = 0
    for d in current:
        j = by_date.get(d["date"])
        if j and d.get("jackpot") != j:
            d["jackpot"] = j
            n += 1
    if n:
        write_json(draws_path(game), current)
    return n


def latest_due_date(game: Game) -> dt.date:
    """Date of the most recent draw whose result should be published by now (21:00 Sydney)."""
    now = now_sydney()
    d = now.date() - dt.timedelta(days=(now.weekday() - game.draw_weekday) % 7)
    if d == now.date() and now.hour < 21:
        d -= dt.timedelta(days=7)
    return d


# -- picks ------------------------------------------------------------------
def picks_dir(game: Game) -> Path:
    return DATA / game.id / "picks"


def load_pick(game: Game, draw: int) -> dict | None:
    return read_json(picks_dir(game) / f"{draw}.json")


def save_pick(game: Game, pick: dict) -> bool:
    return write_json(picks_dir(game) / f"{pick['draw']}.json", pick)


def all_picks(game: Game) -> list[dict]:
    d = picks_dir(game)
    if not d.exists():
        return []
    return sorted((read_json(p) for p in d.glob("*.json")), key=lambda p: p["draw"])


# -- schedule ---------------------------------------------------------------
def next_draw(game: Game, draws: list[dict], open_info: dict | None = None) -> dict:
    """Number and date of the next draw that has no result yet."""
    last = draws[-1] if draws else None
    if open_info and open_info.get("draw") and open_info.get("date") and \
            (not last or int(open_info["draw"]) > last["draw"]):
        number, date = int(open_info["draw"]), dt.date.fromisoformat(open_info["date"])
    else:
        base = dt.date.fromisoformat(last["date"]) if last else now_sydney().date() - dt.timedelta(days=1)
        days = (game.draw_weekday - base.weekday()) % 7 or 7
        date = base + dt.timedelta(days=days)
        # draw numbers usually go up by 1, but Saturday Lotto shares its counter with
        # another weekly draw and goes up by 2 - use the most common recent step
        steps = [b["draw"] - a["draw"] for a, b in zip(draws[-11:], draws[-10:]) if b["draw"] > a["draw"]]
        step = max(set(steps), key=steps.count) if steps else 1
        number = (last["draw"] + step) if last else 0
    lock_at = dt.datetime.combine(date - dt.timedelta(days=1), dt.time(LOCK_HOUR), SYDNEY)
    return {"draw": number, "date": date.isoformat(), "lock_at": lock_at.isoformat(),
            "jackpot": (open_info or {}).get("jackpot")}
