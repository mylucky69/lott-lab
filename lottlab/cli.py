"""Command line entry point:  python -m lottlab <command>

  update            fetch new results, settle locked picks, preview/lock next picks, rebuild site
  backfill [game]   download full history (one page per year)
  add-result GAME DRAW DATE "MAIN" "SUPP"   manual result entry (fallback)
  import-csv GAME FILE                      CSV: draw,date,main,supp  (numbers space separated)
  history [pages]   crawl older draws backwards, a limited number of pages per run
  jackpots [game]   download the advertised jackpot of past draws (lotteryguru, ~10 pages/game)
  backtest [game]   walk-forward experiment (slow, run weekly)
  build             rebuild docs/data/*.json only
"""
from __future__ import annotations

import csv
import datetime as dt
import sys
import traceback

from . import sources as src
from .analysis import backtest, simulate_set, statistics
from .games import GAMES, Game, check_line
from .generator import STRATEGIES, generate
from .value import value as draw_value
from .store import (DATA, ROOT, SITE_DATA, all_picks, latest_due_date, load_draws, load_pick, merge_draws, set_dividends,
                    set_jackpots,
                    next_draw, now_sydney, read_json, save_pick, write_json)

CONFIG = read_json(ROOT / "config.json", {}) or {}
N_LINES = int(CONFIG.get("lines", 50))
RULES_VERSION = 2  # bump when prize rules change, so unlocked previews are rebuilt
def min_jackpot(game: Game) -> float:
    """Play only when the jackpot is at least this (per game in config.json, 0 = always)."""
    v = CONFIG.get("min_jackpot") or 0
    return float(v.get(game.id, 0) if isinstance(v, dict) else v)


def announced_jackpot(game: Game, draw: int) -> float | None:
    jp = read_json(DATA / game.id / "jackpot.json", {}) or {}
    return jp.get("jackpot") if jp.get("for_draw") == draw else None


def play_decision(game: Game, jackpot: float | None) -> dict:
    MIN_JACKPOT = min_jackpot(game)
    if MIN_JACKPOT <= 0:
        return {"play": True, "play_reason": "Chơi mọi kỳ"}
    if jackpot is None:
        return {"play": None, "play_reason": "Chưa có thông tin jackpot"}
    if jackpot >= MIN_JACKPOT:
        return {"play": True, "play_reason": f"Jackpot ${jackpot / 1e6:g} triệu ≥ ${MIN_JACKPOT / 1e6:g} triệu"}
    return {"play": False, "play_reason": f"Jackpot ${jackpot / 1e6:g} triệu < ${MIN_JACKPOT / 1e6:g} triệu"}
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
    # drop unlocked previews that no longer match the next draw (e.g. renumbered)
    for p in all_picks(game):
        if p.get("status") == "preview" and p["draw"] != nxt["draw"]:
            (DATA / game.id / "picks" / f"{p['draw']}.json").unlink(missing_ok=True)
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
    jackpot = announced_jackpot(game, nxt["draw"])
    if existing and existing.get("based_on") == based_on and existing.get("strategy") == STRATEGY \
            and len(existing.get("lines", [])) == N_LINES and existing.get("rules") == RULES_VERSION \
            and now < lock_at:
        decision = {"jackpot": jackpot, **play_decision(game, jackpot), "min_jackpot": min_jackpot(game)}
        if any(existing.get(k) != v for k, v in decision.items()):
            existing.update(decision)
            save_pick(game, existing)
        return existing
    if existing and existing.get("based_on") == based_on and now >= lock_at:
        lines = existing["lines"]
    else:
        lines = generate(game, history, nxt["draw"], STRATEGY, N_LINES)
    status = "locked" if now >= lock_at else "preview"
    if status == "locked" and min_jackpot(game) > 0 and jackpot is None:
        log("  no jackpot information at lock time: marked as not played")
    pick = {
        "game": game.id, "draw": nxt["draw"], "date": nxt["date"], "lock_at": nxt["lock_at"],
        "jackpot": jackpot, **play_decision(game, jackpot), "min_jackpot": min_jackpot(game),
        "status": status, "strategy": STRATEGY,
        "strategy_label": STRATEGIES[STRATEGY], "based_on": based_on,
        "generated_at": (existing or {}).get("generated_at") if existing and existing.get("lines") == lines
        else now.isoformat(timespec="seconds"),
        "locked_at": now.isoformat(timespec="seconds") if status == "locked" else None,
        "lines": lines, "rules": RULES_VERSION,
        "simulation": simulate_set(game, lines),
        "simulation_random": simulate_set(game, generate(game, history, nxt["draw"], "random", N_LINES)),
    }
    if status == "locked" and pick["play"] is None:
        pick.update(play=False, play_reason="Không có thông tin jackpot lúc chốt")
    save_pick(game, pick)
    log(f"  {game.name} #{nxt['draw']} ({nxt['date']}): {status}, play={pick['play']}")
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
                                      "dividends_known", "lines", "checks", "play", "play_reason", "jackpot")}
                for p in picks if p.get("status") == "settled"]
    price = (CONFIG.get("ticket_price") or {}).get(game.id)

    def total(rows):
        t = {"draws": len(rows), "lines": sum(len(r["lines"]) for r in rows),
             "prize": round(sum(r["prize"] or 0 for r in rows), 2),
             "prize_lines": sum(sum(r["divisions"].values()) for r in rows)}
        if price:
            t["cost"] = round(t["lines"] * price, 2)
            t["net"] = round(t["prize"] - t["cost"], 2)
        return t
    totals = total(tracking)
    totals["played"] = total([t for t in tracking if t.get("play") is not False])
    write_json(SITE_DATA / f"{game.id}.json", {
        "meta": game.to_meta(), "status": status, "config": {"lines": N_LINES, "strategy": STRATEGY,
                                                            "ticket_price": price, "min_jackpot": min_jackpot(game)},
        "next": nxt, "latest": list(reversed(draws[-15:])),
        "tracking": list(reversed(tracking)), "totals": totals,
        "stats": statistics(game, draws),
        "backtest": read_json(DATA / game.id / "backtest.json"),
        "models": read_json(DATA / game.id / "models.json"),
        "study": read_json(DATA / game.id / "study.json"),
        "optimize": read_json(DATA / game.id / "optimize.json"),
        "value": _value_for(game, draws, nxt, price),
        "history": {**(read_json(DATA / game.id / "history.json", {}) or {}), "stop": HISTORY_STOP[game.id]},
    })


def _value_for(game: Game, draws: list[dict], nxt: dict | None, price) -> dict | None:
    jp = read_json(DATA / game.id / "jackpot.json", {}) or {}
    jackpot = jp.get("jackpot") if nxt and jp.get("for_draw") == nxt.get("draw") else None
    v = draw_value(game, draws, jackpot, price)
    if v:
        v["jackpot_source"] = "lotteryguru.com" if jackpot else None
    return v


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
def fetch_new(game: Game, st: dict) -> None:
    """Fetch only what is missing: a newly due result, then its prize table."""
    draws = load_draws(game)
    last_date = draws[-1]["date"] if draws else ""
    due = latest_due_date(game).isoformat()
    errors, new_all = [], []

    def merge(rows, name):
        new, conflicts = merge_draws(game, rows)
        new_all.extend(new)
        if conflicts:
            st.setdefault("conflicts", []).extend(conflicts)
            log(f"  CONFLICT ({name}): {conflicts}")
        st.setdefault("sources_ok", []).append(name)
        return new

    if last_date < due:
        log(f"  result due for {due} (have {last_date or 'none'})")
        try:
            merge(src.lotteryextreme_latest(game), "lotteryextreme")
        except src.SourceError as e:
            errors.append(f"lotteryextreme: {e}")
        # second source: cross-check new draws, or fill in if the first failed
        try:
            merge(src.national_lottery_year(game, src_year()), "national-lottery")
        except src.SourceError as e:
            errors.append(f"national-lottery: {e}")
    # prize tables for recent draws (published a little after the numbers)
    recent_cut = (now_sydney().date() - dt.timedelta(days=10)).isoformat()
    for d in [d for d in load_draws(game) if d["date"] >= recent_cut and not d.get("dividends")][-3:]:
        try:
            divs = src.lotteryextreme_dividends(game, d["date"])
            if divs and set_dividends(game, d["draw"], divs):
                log(f"  dividends #{d['draw']}")
                st["changed"] = True
        except src.SourceError as e:
            errors.append(f"dividends #{d['draw']}: {e}")
    # announced jackpot for the next draw (one request per draw)
    have = load_draws(game)
    if have:
        nxt = next_draw(game, have)
        jp_path = DATA / game.id / "jackpot.json"
        jp = read_json(jp_path, {}) or {}
        if jp.get("for_draw") != nxt["draw"]:
            found = None
            try:  # history page 1: next jackpot + jackpots of the last 10 draws
                page = src.lotteryguru_history(game, 1)
                set_jackpots(game, page["draws"])
                found = page["next"]
            except Exception as e:  # optional information
                errors.append(f"jackpot: {e}")
            if not found:
                try:
                    found = src.next_jackpot(game)
                except Exception as e:
                    errors.append(f"jackpot: {e}")
            if found and (not found.get("date") or found["date"] == nxt["date"]):
                write_json(jp_path, {**found, "for_draw": nxt["draw"],
                                     "fetched_at": now_sydney().isoformat(timespec="minutes")})
                log(f"  next jackpot: ${found['jackpot']:,.0f}")
                st["changed"] = True
    if new_all:
        log(f"  new results: {new_all}")
    st["new_draws"] = new_all
    st["changed"] = st.get("changed") or bool(new_all)
    st["fetch_ok"] = bool(have) and have[-1]["date"] >= due
    st["error"] = "; ".join(errors)[:400] or None


def src_year() -> int:
    return now_sydney().year


def backfill_state(game: Game) -> dict:
    return read_json(DATA / game.id / "backfill.json", {"done": [], "last_try": None}) or {}


def backfill_game(game: Game, force: bool = False) -> None:
    state = backfill_state(game)
    today = now_sydney().date().isoformat()
    years = [y for y in range(int(game.history_start), src_year() + 1) if y not in state["done"]]
    if not years or (state.get("last_try") == today and not force):
        return
    state["last_try"] = today
    log(f"  backfill {game.name}: {len(years)} year(s)")
    failures = 0
    for y in years:
        try:
            rows = src.national_lottery_year(game, y)
        except src.SourceError as e:
            if "HTTP 404" in str(e):
                state["done"].append(y)       # no draws that year
                continue
            failures += 1
            log(f"  {y}: {e}")
            if failures >= 3:
                log("  source unavailable, will retry tomorrow")
                break
            continue
        new, conflicts = merge_draws(game, rows)
        log(f"  {y}: {len(rows)} draws, {len(new)} new")
        if y < src_year():
            state["done"].append(y)
    state["done"] = sorted(set(state["done"]))
    write_json(DATA / game.id / "backfill.json", state)


def cmd_update(only: list[str]) -> int:
    statuses = {}
    for game in GAMES.values():
        if only and game.id not in only:
            continue
        log(f"[{game.name}]")
        st = {"fetch_ok": False, "error": None, "changed": False,
              "checked_at": now_sydney().isoformat(timespec="minutes")}
        try:
            backfill_game(game)
            fetch_new(game, st)
        except Exception as e:  # never stop: manual entry still works
            st["error"] = f"{type(e).__name__}: {e}"[:400]
            log(f"  fetch failed: {st['error']}")
        draws = load_draws(game)
        settle(game, draws)
        prepare_next(game, draws, None)
        prev = (read_json(SITE_DATA / f"{game.id}.json", {}) or {}).get("status", {})
        st["last_ok"] = st["checked_at"] if st["fetch_ok"] else prev.get("last_ok")
        bf = backfill_state(game)
        st["history_years"] = len(bf.get("done", []))
        build_game(game, st)
        statuses[game.id] = st
    build_index(statuses)
    return 0


def cmd_backfill(only: list[str]) -> int:
    for game in GAMES.values():
        if not only or game.id in only:
            backfill_game(game, force=True)
    return 0


# How far back the per-draw crawl goes (current number matrix only, it is what the stats use).
HISTORY_STOP = {"oz": "2022-05-31", "powerball": "2018-04-19", "saturday": "2000-01-01"}


def fill_dividends(game: Game, budget: int) -> int:
    """Fetch the prize table for stored draws that do not have one yet (newest first).
    Each draw is tried once; draws whose page has no prize table are not retried."""
    path = DATA / game.id / "history.json"
    state = read_json(path, {}) or {}
    tried = set(state.get("dividends_tried", []))
    used = 0
    missing = [d for d in load_draws(game) if not d.get("dividends") and d["draw"] not in tried]
    for d in reversed(missing):
        if used >= budget:
            break
        used += 1
        try:
            draw, _ = src.lotteryextreme_draw(game, d["date"])
        except src.SourceError as e:
            log(f"  dividends {d['date']}: {e}")
            if "HTTP 404" not in str(e):
                break
            draw = None
        if draw and draw["draw"] == d["draw"] and draw["dividends"]:
            set_dividends(game, d["draw"], draw["dividends"])
        else:
            tried.add(d["draw"])
    if used:
        log(f"  {game.name}: filled prize tables, {used} page(s)")
        state = read_json(path, {}) or {}
        state["dividends_tried"] = sorted(tried)
        write_json(path, state)
    return used


def history_game(game: Game, budget: int) -> int:
    """Walk lotteryextreme's per-draw pages backwards from the oldest stored draw.
    Returns pages used. Progress is saved so the next run continues where this one stopped."""
    used = fill_dividends(game, budget)
    budget -= used
    path = DATA / game.id / "history.json"
    state = read_json(path, {}) or {}
    if state.get("done") or budget <= 0:
        return used
    stop = HISTORY_STOP[game.id]
    draws = load_draws(game, valid_only=False)
    if not state.get("cursor"):
        if not draws:
            return used
        oldest = dt.date.fromisoformat(draws[0]["date"])
        state["cursor"] = (oldest - dt.timedelta(days=7)).isoformat()
    start_used = used
    budget += used
    misses, added = state.get("misses", 0), 0
    try:
        while used < budget and state["cursor"] >= stop:
            used += 1
            try:
                draw, prev = src.lotteryextreme_draw(game, state["cursor"])
            except src.SourceError as e:
                if "HTTP 404" not in str(e) and "HTTP 410" not in str(e):
                    log(f"  {state['cursor']}: {e} (stopping this run)")
                    break
                draw, prev = None, None
            if draw:
                misses = 0
                new, conflicts = merge_draws(game, [draw])
                if new:
                    added += 1
                elif draw.get("dividends"):
                    set_dividends(game, draw["draw"], draw["dividends"])
                for c in conflicts:
                    log(f"  conflict {c}")
            else:
                misses += 1
                if misses >= 6:
                    log("  six missing weeks in a row, history finished")
                    state["done"] = True
                    break
            cur = dt.date.fromisoformat(state["cursor"])
            nxt = prev or (cur - dt.timedelta(days=7)).isoformat()
            state["cursor"] = nxt if nxt < state["cursor"] else (cur - dt.timedelta(days=7)).isoformat()
        if state["cursor"] < stop:
            state["done"] = True
    finally:
        state["misses"] = misses
        write_json(path, state)
    log(f"  {game.name}: {used - start_used} page(s), {added} new draw(s), next {state['cursor']}"
        + (" - finished" if state.get("done") else ""))
    return used


def cmd_jackpots(args: list[str]) -> int:
    """Download the jackpot of every draw lotteryguru lists (about 10 pages per game, once)."""
    src.PAUSE = 4.0
    for game in GAMES.values():
        if args and game.id not in args:
            continue
        state_path = DATA / game.id / "jackpots.json"
        state = read_json(state_path, {}) or {}
        if state.get("done"):
            continue
        first = src.lotteryguru_history(game, 1)
        n = set_jackpots(game, first["draws"])
        pages = range(max(2, state.get("next_page", 2)), first["last_page"] + 1)
        for p in pages:
            try:
                n += set_jackpots(game, src.lotteryguru_history(game, p)["draws"])
            except src.SourceError as e:
                log(f"  page {p}: {e}")
                state["next_page"] = p
                break
        else:
            state["done"] = True
        write_json(state_path, state)
        log(f"  {game.name}: jackpots set on {n} draw(s)")
    return cmd_build([])


def cmd_history(args: list[str]) -> int:
    budget = int(args[0]) if args else 300
    src.PAUSE = 4.0
    for game in GAMES.values():
        if budget <= 0:
            break
        budget -= history_game(game, budget)
    return cmd_build([])


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
    _, conflicts = merge_draws(game, [d])
    if conflicts:
        log(f"Not added, differs from stored result: {conflicts}")
        return 1
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
    new, conflicts = merge_draws(game, rows)
    for c in conflicts:
        log(f"  conflict {c}")
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
            "import-csv": cmd_import_csv, "backtest": cmd_backtest, "build": cmd_build,
            "history": cmd_history, "jackpots": cmd_jackpots}
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
