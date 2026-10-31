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


def merge_draws(game: Game, new: list[dict]) -> list[int]:
    """Add/refresh draws. Returns draw numbers that were new."""
    current = {d["draw"]: d for d in read_json(draws_path(game), [])}
    added = []
    for d in new:
        if d["draw"] not in current:
            added.append(d["draw"])
            current[d["draw"]] = d
        elif d.get("source") == "thelott":
            # official data always wins over manual entry and refreshes late dividends
            current[d["draw"]] = d
    write_json(draws_path(game), sorted(current.values(), key=lambda d: d["draw"]))
    return sorted(added)


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
        number = (last["draw"] + 1) if last else 0
    lock_at = dt.datetime.combine(date - dt.timedelta(days=1), dt.time(LOCK_HOUR), SYDNEY)
    return {"draw": number, "date": date.isoformat(), "lock_at": lock_at.isoformat(),
            "jackpot": (open_info or {}).get("jackpot")}
