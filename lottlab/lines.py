"""How many lines per draw?   python -m lottlab.lines

For each game, at the configured jackpot threshold and typical qualifying jackpot:
  value per $1 of the recommended set of n lines (fewer lines can all use the least
  popular numbers; more lines must also use popular ones), chance of at least one prize,
  chance of the jackpot, expected loss per year, and the real replay on qualifying draws.
"""
from __future__ import annotations

import statistics
import sys

import numpy as np

from .analysis import simulate_set
from .coverage import prize_stats
from .cli import CONFIG, min_jackpot
from .games import GAMES, Game, check_line
from .generator import generate
from .popularity import set_value_exact
from .store import DATA, load_draws, read_json, write_json

SIZES = [1, 5, 10, 15, 20, 25, 30, 35, 50, 75, 100]


def study_lines(game: Game, price: float) -> dict | None:
    draws = load_draws(game)
    opt = read_json(DATA / game.id / "optimize.json")
    if not opt or "sales_model" not in opt:
        return None
    t = min_jackpot(game)
    q_idx = [i for i, d in enumerate(draws) if d.get("jackpot") and d.get("dividends") and d["jackpot"] >= t]
    if not q_idx:
        return None
    J = float(np.median([draws[i]["jackpot"] for i in q_idx]))
    sm = opt["sales_model"]
    sales = sm["a"] + sm["b"] * J
    row = next((s for s in opt["sweep"] if s["threshold"] >= t), opt["sweep"][-1])
    per_year = row["per_year"]
    recent = [d for d in draws if d.get("dividends")][-30:]
    pay = {o["division"]: statistics.median([x["amount"] for d in recent for x in d["dividends"]
                                             if x["div"] == o["division"] and x.get("amount")] or [0])
           for o in game.division_odds()}
    p1 = game.division_odds()[0]["p"]
    out = []
    for n in SIZES:
        lines = generate(game, draws, draws[-1]["draw"] + 1, "balanced", n, candidates=2000, polish=200)
        sv = set_value_exact(game, lines, pay, sales, J)
        v = sv["value_set"] / price
        sim = simulate_set(game, lines, 20000)
        ps = prize_stats(game, lines, pay, price, n=60000)
        cost = n * price
        # real replay on qualifying draws (numbers only from data before each draw)
        won = spent = 0.0
        for i in q_idx:
            d = draws[i]
            L = generate(game, draws[:i], d["draw"], "balanced", n, candidates=1200, polish=100)
            paid = {x["div"]: float(x.get("amount") or 0) for x in d["dividends"]}
            for line in L:
                div = check_line(game, line, d)["div"]
                if div and div != 1:
                    won += paid.get(div, 0.0)
            spent += cost
        years = len(q_idx) / per_year if per_year else 1
        out.append({
            "lines": n, "cost_per_draw": round(cost, 2), "per_dollar": round(v, 3),
            "p_any_prize": round(ps["p_any"], 4), "p_recoup": round(ps["p_recoup"], 4), "p_jackpot": n * p1,
            "jackpot_one_in": round(1 / (n * p1)),
            "expected_loss_per_draw": round(cost * (1 - v), 2),
            "expected_loss_per_year": round(cost * (1 - v) * per_year, 0),
            "spend_per_year": round(cost * per_year, 0),
            "real_net": round(won - spent, 2), "real_net_per_year": round((won - spent) / years, 0),
            "line_popularity": round(sv["line_pop"], 3),
        })
    # calibration: does the predicted chance of >= 1 prize match real draws?
    calib = []
    for n in (10, 20, 35, 50):
        pred = next((r["p_any_prize"] for r in out if r["lines"] == n), None)
        hits = tested = 0
        for i in range(30, len(draws)):
            d = draws[i]
            L = generate(game, draws[:i], d["draw"], "balanced", n, candidates=800, polish=50)
            hits += any(check_line(game, l, d)["div"] for l in L)
            tested += 1
        calib.append({"lines": n, "predicted": pred, "real": round(hits / tested, 4), "draws": tested, "hits": hits})
    return {"game": game.id, "threshold": t, "typical_jackpot": J, "draws_per_year": per_year,
            "qualifying_draws": len(q_idx), "price": price, "sizes": out, "calibration": calib}


if __name__ == "__main__":
    prices = CONFIG.get("ticket_price") or {}
    for gid in sys.argv[1:] or list(GAMES):
        g = GAMES[gid]
        res = study_lines(g, prices[gid])
        if not res:
            print(f"[{g.name}] no data"); continue
        write_json(DATA / g.id / "lines.json", res)
        print(f"\n[{g.name}] jackpot ≥ ${res['threshold']/1e6:.0f}M (typical ${res['typical_jackpot']/1e6:.0f}M), "
              f"{res['draws_per_year']} draws/yr, {res['qualifying_draws']} past qualifying draws")
        print(f"  {'lines':>5} {'cost':>7} {'$/1':>5} {'P(prize)':>8} {'recoup':>7} {'jackpot 1 in':>13} {'loss/draw':>9} {'loss/yr':>8} {'real/yr':>8} {'pop':>5}")
        for r in res["sizes"]:
            print(f"  {r['lines']:>5} {r['cost_per_draw']:>7.2f} {r['per_dollar']:>5.3f} {r['p_any_prize']:>8.1%} {r['p_recoup']:>7.1%} "
                  f"{r['jackpot_one_in']:>13,} {r['expected_loss_per_draw']:>9.2f} {r['expected_loss_per_year']:>8,.0f} "
                  f"{r['real_net_per_year']:>8,.0f} {r['line_popularity']:>5.2f}")
        for c in res["calibration"]:
            print(f"  calibration {c['lines']} lines: predicted {c['predicted']:.1%}, real {c['real']:.1%} ({c['hits']}/{c['draws']} draws)")
