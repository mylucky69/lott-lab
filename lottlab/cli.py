"""Command line entry point:  python -m lottlab <command>

  update            fetch new results, settle locked picks, preview/lock next picks, rebuild site
  backfill [game]   download full history of the current matrix
  add-result GAME DRAW DATE "MAIN" "SUPP"   manual result entry (fallback)
  import-csv GAME FILE                      CSV: draw,date,main,supp  (numbers space separated)
  backtest [game]   walk-forward experiment (slow, run weekly)
  build             rebuild docs/data/*.json only
"""
from __future__ import annotations

import csv
import datetime as dt
import sys
import traceback

from . import fetch
from .analysis import backtest, simulate_set, statistics
from .games import GAMES, Game, check_line
from .generator import STRATEGIES, generate
from .store import (DATA, ROOT, SITE_DATA, all_picks, load_draws, load_pick, merge_draws,
                    next_draw, now_sydney, read_json, save_pick, write_json)

CONFIG = read_json(ROOT / "config.json", {}) or {}
N_LINES = int(CONFIG.get("lines", 50))
STRATEGY = CONFIG.get("strategy", "balanced")


def log(*a):
    print(*a, flush=True)


# -- settle locked picks once a result is known -------------------------------
def settle(game: Game, draws: list[dict]) -> None:
    by_num = {d["draw"]: d for d in draws}
    by_date = {d["date"]: d for d in draws}
    for p in all_picks(game):
        if p.get("status") == "settled":
            continue
        d = by_num.get(p["draw"]) or by_date.get(p["date"])
        if not d:
            continue
        amounts = {x["div"]: x.get("amount") for x in d.get("dividends", [])}
        results, divs, prize = [], {}, 0.0
        for l in p["lines"]:
            r = check_line(game, l, d)
            results.append(r)
            if r["div"]:
                divs[str(r["div"])] = divs.get(str(r["div"]), 0) + 1
                prize += float(amounts.get(r["div"]) or 0)
        p.update(status="settled" if p.get("status") == "locked" else "expired",
                 result={"draw": d["draw"], "date": d["date"], "main": d["main"], "supp": d["supp"]},
                 checks=results, divisions=divs, prize=round(prize, 2),
                 best=max((r["m"] for r in results), default=0),
                 dividends_known=bool(amounts))
        save_pick(game, p)
        log(f"  settled {game.name} #{p['draw']}: {divs or 'no prize'}")


# -- preview / lock the next draw ---------------------------------------------
def prepare_next(game: Game, draws: list[dict], open_info: dict | None) -> dict | None:
    if not draws:
        return None
    nxt = next_draw(game, draws, open_info)
    existing = load_pick(game, nxt["draw"])
    now = now_sydney()
    lock_at = dt.datetime.fromisoformat(nxt["lock_at"])
    if existing and existing.get("status") in ("locked", "settled"):
        if nxt.get("jackpot") and existing.get("jackpot") != nxt["jackpot"]:
            existing["jackpot"] = nxt["jackpot"]
            save_pick(game, existing)
        return existing
    history = [d for d in draws if d["date"] < nxt["date"]]
    based_on = history[-1]["draw"] if history else None
    if existing and existing.get("based_on") == based_on and existing.get("strategy") == STRATEGY \
            and len(existing.get("lines", [])) == N_LINES and now < lock_at:
        return existing
    if existing and existing.get("based_on") == based_on and now >= lock_at:
        lines = existing["lines"]
    else:
        lines = generate(game, history, nxt["draw"], STRATEGY, N_LINES)
    status = "locked" if now >= lock_at else "preview"
    pick = {
        "game": game.id, "draw": nxt["draw"], "date": nxt["date"], "lock_at": nxt["lock_at"],
        "jackpot": nxt.get("jackpot"), "status": status, "strategy": STRATEGY,
        "strategy_label": STRATEGIES[STRATEGY], "based_on": based_on,
        "generated_at": (existing or {}).get("generated_at") if existing and existing.get("lines") == lines
        else now.isoformat(timespec="seconds"),
        "locked_at": now.isoformat(timespec="seconds") if status == "locked" else None,
        "lines": lines,
        "simulation": simulate_set(game, lines),
        "simulation_random": simulate_set(game, generate(game, history, nxt["draw"], "random", N_LINES)),
    }
    save_pick(game, pick)
    log(f"  {game.name} #{nxt['draw']} ({nxt['date']}): {status}")
    return pick


# -- site data ----------------------------------------------------------------
def build_game(game: Game, status: dict) -> None:
    draws = load_draws(game)
    picks = all_picks(game)
    nxt = None
    pending = [p for p in picks if p.get("status") in ("preview", "locked")]
    if pending:
        nxt = pending[-1]
    tracking = [{k: p.get(k) for k in ("draw", "date", "status", "divisions", "prize", "best", "result",
                                      "dividends_known", "lines", "checks")}
                for p in picks if p.get("status") == "settled"]
    totals = {"draws": len(tracking), "lines": sum(len(t["lines"]) for t in tracking),
              "prize": round(sum(t["prize"] or 0 for t in tracking), 2),
              "prize_lines": sum(sum(t["divisions"].values()) for t in tracking)}
    price = (CONFIG.get("ticket_price") or {}).get(game.id)
    if price:
        totals["cost"] = round(totals["lines"] * price, 2)
    write_json(SITE_DATA / f"{game.id}.json", {
        "meta": game.to_meta(), "status": status, "config": {"lines": N_LINES, "strategy": STRATEGY,
                                                            "ticket_price": price},
        "next": nxt, "latest": list(reversed(draws[-15:])),
        "tracking": list(reversed(tracking)), "totals": totals,
        "stats": statistics(game, draws),
        "backtest": read_json(DATA / game.id / "backtest.json"),
    })


def build_index(statuses: dict) -> None:
    prev = read_json(SITE_DATA / "index.json", {}) or {}
    changed = any(s.get("changed") for s in statuses.values())
    write_json(SITE_DATA / "index.json", {
        "games": [g.id for g in GAMES.values()],
        "checked_at": now_sydney().isoformat(timespec="minutes"),
        "updated_at": now_sydney().isoformat(timespec="minutes") if changed or not prev
        else prev.get("updated_at"),
    })


# -- commands -------------------------------------------------------------------
def cmd_update(only: list[str]) -> int:
    statuses, failures = {}, 0
    for game in GAMES.values():
        if only and game.id not in only:
            continue
        log(f"[{game.name}]")
        st = {"fetch_ok": False, "error": None, "changed": False,
              "checked_at": now_sydney().isoformat(timespec="minutes")}
        open_info = None
        try:
            if not load_draws(game, valid_only=False):
                cmd_backfill([game.id])
            new = merge_draws(game, fetch.latest(game, 5))
            st.update(fetch_ok=True, new_draws=new, changed=bool(new))
            if new:
                log(f"  new results: {new}")
            open_info = fetch.open_draw(game)
        except Exception as e:  # keep going: manual results still work
            failures += 1
            st["error"] = f"{type(e).__name__}: {e}"[:300]
            log(f"  fetch failed: {st['error']}")
        draws = load_draws(game)
        settle(game, draws)
        prepare_next(game, draws, open_info)
        prev = (read_json(SITE_DATA / f"{game.id}.json", {}) or {}).get("status", {})
        if not st["fetch_ok"] and prev.get("last_ok"):
            st["last_ok"] = prev["last_ok"]
        elif st["fetch_ok"]:
            st["last_ok"] = st["checked_at"]
        build_game(game, st)
        statuses[game.id] = st
    build_index(statuses)
    return 0


def cmd_backfill(only: list[str]) -> int:
    today = now_sydney().date()
    for game in GAMES.values():
        if only and game.id not in only:
            continue
        start = dt.date.fromisoformat(game.history_start)
        log(f"  backfill {game.name} from {start}")
        got = fetch.history(game, start, today)
        new = merge_draws(game, got)
        log(f"  {len(got)} draws fetched, {len(new)} new")
    return 0


def _nums(s: str) -> list[int]:
    return [int(x) for x in s.replace(",", " ").split()]


def cmd_add_result(args: list[str]) -> int:
    game_id, draw, date, main, supp = args[:5]
    game = GAMES[game_id]
    d = {"game": game.id, "draw": int(draw), "date": date, "main": _nums(main), "supp": _nums(supp),
         "dividends": [], "source": "manual"}
    from .games import valid_draw
    if not valid_draw(game, d):
        log(f"Invalid result for {game.name}: need {game.drawn} main numbers 1..{game.pool} and "
            + (f"1 Powerball 1..{game.pb_pool}" if game.pb_pool else f"{game.supps} supplementary"))
        return 1
    merge_draws(game, [d])
    log(f"Added {game.name} #{draw}")
    return cmd_update([game.id]) if "--no-update" not in args else 0


def cmd_import_csv(args: list[str]) -> int:
    game = GAMES[args[0]]
    rows = []
    with open(args[1], newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({"game": game.id, "draw": int(r["draw"]), "date": r["date"][:10],
                         "main": _nums(r["main"]), "supp": _nums(r.get("supp", "")),
                         "dividends": [], "source": "csv"})
    new = merge_draws(game, rows)
    log(f"Imported {len(rows)} rows, {len(new)} new")
    return 0


def cmd_backtest(only: list[str]) -> int:
    for game in GAMES.values():
        if only and game.id not in only:
            continue
        draws = load_draws(game)
        log(f"  backtest {game.name} on {len(draws)} draws")
        res = backtest(game, draws, n_lines=N_LINES)
        res["run_at"] = now_sydney().isoformat(timespec="minutes")
        write_json(DATA / game.id / "backtest.json", res)
    return cmd_build([])


def cmd_build(_args) -> int:
    for game in GAMES.values():
        prev = (read_json(SITE_DATA / f"{game.id}.json", {}) or {}).get("status", {})
        build_game(game, prev)
    build_index({})
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmds = {"update": cmd_update, "backfill": cmd_backfill, "add-result": cmd_add_result,
            "import-csv": cmd_import_csv, "backtest": cmd_backtest, "build": cmd_build}
    if argv[0] not in cmds:
        print(__doc__)
        return 1
    try:
        return cmds[argv[0]](argv[1:])
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
