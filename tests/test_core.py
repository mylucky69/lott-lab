"""Run: python -m pytest -q   (uses random fake draws in a temp folder, never real data)."""
import datetime as dt
import importlib
import json
import os

import numpy as np
import pytest


def fake_draws(game, n, start="2024-01-02"):
    rng = np.random.default_rng(0)
    d0 = dt.date.fromisoformat(start)
    d0 += dt.timedelta(days=(game.draw_weekday - d0.weekday()) % 7)
    out = []
    for i in range(n):
        nums = rng.permutation(np.arange(1, game.pool + 1))
        main = sorted(nums[:game.drawn].tolist())
        supp = [int(rng.integers(1, game.pb_pool + 1))] if game.pb_pool else nums[game.drawn:game.drawn + game.supps].tolist()
        out.append({"game": game.id, "draw": 1000 + i, "date": (d0 + dt.timedelta(weeks=i)).isoformat(),
                    "main": main, "supp": supp, "dividends": [], "source": "test"})
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTT_ROOT", str(tmp_path))
    import lottlab.store, lottlab.cli
    importlib.reload(lottlab.store)
    importlib.reload(lottlab.cli)
    return tmp_path, lottlab.cli, lottlab.store


def test_official_div1_odds():
    from lottlab.games import GAMES
    one_in = {g: GAMES[g].division_odds()[0]["one_in"] for g in GAMES}
    assert one_in == {"oz": 62891499.0, "powerball": 134490400.0, "saturday": 8145060.0}


def test_check_line():
    from lottlab.games import GAMES, check_line
    oz, pb, sat = GAMES["oz"], GAMES["powerball"], GAMES["saturday"]
    d = {"main": [1, 2, 3, 4, 5, 6, 7], "supp": [8, 9, 10]}
    assert check_line(oz, {"main": [1, 2, 3, 4, 5, 6, 8]}, d)["div"] == 2
    assert check_line(oz, {"main": [1, 2, 3, 8, 20, 21, 22]}, d)["div"] == 7
    assert check_line(oz, {"main": [1, 2, 3, 20, 21, 22, 23]}, d)["div"] is None
    assert check_line(pb, {"main": [1, 2, 20, 21, 22, 23, 24], "pb": 5}, {"main": [1, 2, 3, 4, 5, 6, 7], "supp": [5]})["div"] == 9
    sd = {"main": [1, 2, 3, 4, 5, 6], "supp": [7, 8]}
    assert check_line(sat, {"main": [1, 7, 8, 30, 31, 32]}, sd)["div"] == 6


def test_generator_shape_and_coverage():
    from lottlab.games import GAMES
    from lottlab.generator import generate
    for g in GAMES.values():
        hist = fake_draws(g, 60)
        lines = generate(g, hist, 2000, "balanced", 50, candidates=800, polish=150)
        assert len(lines) == 50
        assert len({tuple(l["main"]) for l in lines}) == 50
        for l in lines:
            assert len(set(l["main"])) == g.pick and all(1 <= n <= g.pool for n in l["main"])
        used = {n for l in lines for n in l["main"]}
        assert used == set(range(1, g.pool + 1))           # every number covered
        if g.pb_pool:
            assert {l["pb"] for l in lines} == set(range(1, g.pb_pool + 1))
        # deterministic
        assert lines == generate(g, hist, 2000, "balanced", 50, candidates=800, polish=150)


def test_pipeline_preview_lock_settle(env, monkeypatch):
    root, cli, store = env
    from lottlab.games import GAMES
    import lottlab.sources as src
    def offline(*a, **k):
        raise src.SourceError("offline")
    for name in ("lotteryextreme_latest", "national_lottery_year", "lotteryextreme_dividends"):
        monkeypatch.setattr(cli.src, name, offline)
    oz = GAMES["oz"]
    draws = fake_draws(oz, 40)
    store.merge_draws(oz, draws)
    last = draws[-1]
    draw_day = dt.date.fromisoformat(last["date"]) + dt.timedelta(days=7)
    # two days before the draw: preview
    monkeypatch.setenv("LOTT_NOW", f"{draw_day - dt.timedelta(days=2)}T10:00")
    cli.cmd_update(["oz"])
    p = store.load_pick(oz, last["draw"] + 1)
    assert p["status"] == "preview" and len(p["lines"]) == 50
    # day before, after 17:00: locked, same lines
    monkeypatch.setenv("LOTT_NOW", f"{draw_day - dt.timedelta(days=1)}T17:30")
    cli.cmd_update(["oz"])
    p2 = store.load_pick(oz, last["draw"] + 1)
    assert p2["status"] == "locked" and p2["lines"] == p["lines"]
    # result arrives (manual entry) -> settled, next draw previewed
    monkeypatch.setenv("LOTT_NOW", f"{draw_day}T22:00")
    rc = cli.cmd_add_result(["oz", str(last["draw"] + 1), draw_day.isoformat(),
                             "1 2 3 4 5 6 7", "8 9 10"])
    assert rc == 0
    p3 = store.load_pick(oz, last["draw"] + 1)
    assert p3["status"] == "settled" and len(p3["checks"]) == 50
    assert store.load_pick(oz, last["draw"] + 2)["status"] == "preview"
    site = json.loads((root / "docs/data/oz.json").read_text())
    assert site["totals"]["draws"] == 1 and site["next"]["draw"] == last["draw"] + 2
    assert site["status"]["error"] is None or "offline" in (site["status"]["error"] or "")


def test_backtest_runs():
    from lottlab.analysis import backtest
    from lottlab.games import GAMES
    g = GAMES["saturday"]
    res = backtest(g, fake_draws(g, 50), last_n=10)
    assert set(res["strategies"]) == {"balanced", "hot", "cold", "random"}
    assert res["strategies"]["random"]["lines"] == 500


FIX = __import__("pathlib").Path(__file__).parent / "fixtures"


def test_parsers_on_saved_pages():
    from lottlab.games import GAMES, valid_draw
    from lottlab import sources as src
    for g in ("oz", "powerball", "saturday"):
        rows = src.parse_lotteryextreme(GAMES[g], (FIX / f"le_{g}.html").read_text())
        assert len(rows) == 20 and all(valid_draw(GAMES[g], r) for r in rows)
        assert [r["draw"] for r in rows] == sorted((r["draw"] for r in rows), reverse=True)
    oz = src.parse_national_lottery(GAMES["oz"], (FIX / "nl_oz_2025.html").read_text())
    assert len(oz) == 52 and oz[0]["draw"] == 1663 and oz[0]["date"] == "2025-12-30"
    assert oz[0]["main"] == [11, 12, 14, 19, 20, 27, 31] and oz[0]["supp"] == [1, 13, 43]
    divs = src.parse_le_dividends((FIX / "le_oz_dividends.html").read_text())
    assert divs[1] == {"div": 2, "winners": 11, "amount": 57815.9} and len(divs) == 7


def test_merge_cross_check(env):
    root, cli, store = env
    from lottlab.games import GAMES
    oz = GAMES["oz"]
    a = {"game": "oz", "draw": 1, "date": "2026-01-06", "main": [1, 2, 3, 4, 5, 6, 7], "supp": [8, 9, 10],
         "dividends": [], "source": "lotteryextreme"}
    assert store.merge_draws(oz, [a]) == ([1], [])
    assert store.merge_draws(oz, [{**a, "source": "national-lottery"}]) == ([], [])
    assert store.load_draws(oz)[0]["sources"] == ["lotteryextreme", "national-lottery"]
    new, conflicts = store.merge_draws(oz, [{**a, "main": [1, 2, 3, 4, 5, 6, 11], "source": "x"}])
    assert new == [] and len(conflicts) == 1


def test_history_walks_back_and_stops(env, monkeypatch):
    root, cli, store = env
    from lottlab.games import GAMES
    oz = GAMES["oz"]
    store.merge_draws(oz, [{"game": "oz", "draw": 1700, "date": "2026-09-15", "main": [1, 2, 3, 4, 5, 6, 7],
                            "supp": [8, 9, 10], "dividends": [], "source": "t"}])
    def fake(game, date):
        d = dt.date.fromisoformat(date)
        n = 1700 - (dt.date(2026, 9, 15) - d).days // 7
        if n % 10 == 3:
            raise cli.src.SourceError("HTTP 404")
        return ({"game": "oz", "draw": n, "date": date, "main": [1, 2, 3, 4, 5, 6, 7], "supp": [8, 9, 10],
                 "dividends": [{"div": 1, "winners": 0, "amount": 0.0}], "source": "le"},
                (d - dt.timedelta(days=7)).isoformat())
    monkeypatch.setattr(cli.src, "lotteryextreme_draw", fake)
    monkeypatch.setitem(cli.HISTORY_STOP, "oz", "2026-06-01")
    assert cli.history_game(oz, 5) == 5                       # budget respected
    cli.history_game(oz, 100)
    draws = store.load_draws(oz, valid_only=False)
    assert draws[0]["date"] >= "2026-06-01" and len(draws) > 10
    assert store.read_json(root / "data/oz/history.json")["done"] is True
    assert cli.history_game(oz, 100) == 0                     # nothing more once finished
