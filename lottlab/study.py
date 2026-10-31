"""Study: play only when the jackpot is at least a threshold.   python -m lottlab.study

For each threshold:
  realised  - replay the 50 lines the system would have locked on every qualifying
              draw, real prizes and real ticket prices
  expected  - value of one line on each qualifying draw, from that draw's real jackpot
              and real sales (sales = lower-division winners / probability)
  simulated - many simulated years of following the rule (real qualifying draws are
              resampled), giving the chance of being ahead after 1, 5, 10, 30 years
"""
from __future__ import annotations

import json
import math
import statistics
import sys

import numpy as np

from .cli import CONFIG
from .games import GAMES, Game, check_line
from .generator import generate
from .store import DATA, load_draws, write_json

THRESHOLDS = [0, 10e6, 20e6, 30e6, 50e6, 75e6, 100e6]
YEARS = [1, 5, 10, 30]


def draw_sales(game: Game, d: dict) -> float | None:
    odds = {o["division"]: o["p"] for o in game.division_odds()}
    est = [x["winners"] / odds[x["div"]] for x in d.get("dividends", [])
           if x["div"] >= 3 and (x.get("winners") or 0) >= 50 and x["div"] in odds]
    return statistics.median(est) if est else None


def line_value(game: Game, d: dict) -> float | None:
    """Fair value of one line on this draw (lower divisions as actually paid + jackpot share)."""
    odds = game.division_odds()
    sales = draw_sales(game, d)
    if not sales or not d.get("jackpot"):
        return None
    paid = {x["div"]: float(x.get("amount") or 0) for x in d["dividends"]}
    low = sum(o["p"] * paid.get(o["division"], 0.0) for o in odds[1:])
    p1 = odds[0]["p"]
    lam = sales * p1
    share = (1 - math.exp(-lam)) / lam if lam > 0 else 1.0
    return low + p1 * d["jackpot"] * share


def study(game: Game, price: float, n_lines: int = 50, sims: int = 20000, seed: int = 1) -> dict:
    draws = load_draws(game)
    known = [i for i, d in enumerate(draws) if d.get("jackpot") and d.get("dividends")]
    if not known:
        return {"game": game.id, "draws_with_jackpot": 0}
    span_years = (np.datetime64(draws[known[-1]]["date"]) - np.datetime64(draws[known[0]]["date"])).astype(int) / 365.25
    draws_per_year = 52 if game.id != "saturday" else 52
    odds = game.division_odds()
    rng = np.random.default_rng(seed)
    rows = []
    for t in THRESHOLDS:
        q = [i for i in known if draws[i]["jackpot"] >= t]
        if not q:
            rows.append({"threshold": t, "draws": 0})
            continue
        # realised replay
        cost = won = 0.0
        prize_lines = 0
        for i in q:
            d = draws[i]
            lines = generate(game, draws[:i], d["draw"], "balanced", n_lines, candidates=1200, polish=150)
            paid = {x["div"]: float(x.get("amount") or 0) for x in d["dividends"]}
            for line in lines:
                div = check_line(game, line, d)["div"]
                if div:
                    prize_lines += 1
                    won += paid.get(div, 0.0) if div != 1 else 0.0
            cost += n_lines * price
        values = [v for v in (line_value(game, draws[i]) for i in q) if v]
        per_dollar = float(np.mean(values) / price) if values else None
        # simulation: each year, play the qualifying share of draws, resampling real draws
        frac = len(q) / len(known)
        per_draw_cost = n_lines * price
        p = np.array([o["p"] for o in odds])
        sample_pay, sample_jp, sample_sales = [], [], []
        for i in q:
            d = draws[i]
            paid = {x["div"]: float(x.get("amount") or 0) for x in d["dividends"]}
            sample_pay.append([paid.get(o["division"], 0.0) for o in odds])
            sample_jp.append(d["jackpot"])
            sample_sales.append(draw_sales(game, d) or 0)
        sample_pay, sample_jp, sample_sales = map(np.array, (sample_pay, sample_jp, sample_sales))
        chances = {}
        max_years = max(YEARS)
        n_draws = rng.binomial(draws_per_year, frac, size=(sims, max_years))  # qualifying draws per year
        totals = np.zeros((sims, max_years))
        for y in range(max_years):
            k = n_draws[:, y]
            total_k = int(k.sum())
            pick = rng.integers(0, len(q), total_k)
            wins = rng.poisson(n_lines * p[None, :], size=(total_k, len(p)))
            pay = sample_pay[pick].copy()
            lam = sample_sales[pick] * p[0]
            others = rng.poisson(lam)
            pay[:, 0] = sample_jp[pick] / (others + 1)
            value = (wins * pay).sum(1) - per_draw_cost
            owner = np.repeat(np.arange(sims), k)
            totals[:, y] = np.bincount(owner, weights=value, minlength=sims)
        cum = totals.cumsum(1)
        for yrs in YEARS:
            chances[str(yrs)] = {"p_profit": float((cum[:, yrs - 1] > 0).mean()),
                                 "median": float(np.median(cum[:, yrs - 1])),
                                 "spent": float(n_draws[:, :yrs].sum(1).mean() * per_draw_cost)}
        rows.append({
            "threshold": t, "draws": len(q), "share_of_draws": round(frac, 3),
            "realised": {"cost": round(cost, 2), "won": round(won, 2), "net": round(won - cost, 2),
                         "prize_lines": prize_lines},
            "expected_per_dollar": round(per_dollar, 3) if per_dollar else None,
            "best_draw_per_dollar": round(max(values) / price, 3) if values else None,
            "simulated": chances,
        })
    return {"game": game.id, "price": price, "lines": n_lines, "draws_with_jackpot": len(known),
            "from": draws[known[0]]["date"], "to": draws[known[-1]]["date"],
            "years_covered": round(float(span_years), 1), "thresholds": rows}


if __name__ == "__main__":
    prices = CONFIG.get("ticket_price") or {}
    ids = sys.argv[1:] or list(GAMES)
    for gid in ids:
        g = GAMES[gid]
        res = study(g, prices[gid])
        write_json(DATA / g.id / "study.json", res)
        print(f"[{g.name}] {res.get('draws_with_jackpot')} draws with known jackpot "
              f"({res.get('from')} → {res.get('to')})")
        for r in res.get("thresholds", []):
            if not r.get("draws"):
                print(f"  ≥ ${r['threshold']/1e6:.0f}M: no draws"); continue
            s10 = r["simulated"]["10"]; s30 = r["simulated"]["30"]
            print(f"  ≥ ${r['threshold']/1e6:>3.0f}M: {r['draws']:3d} draws ({r['share_of_draws']:.0%}) | "
                  f"realised net ${r['realised']['net']:>9,.0f} on ${r['realised']['cost']:,.0f} | "
                  f"EV/$ {r['expected_per_dollar']} (best {r['best_draw_per_dollar']}) | "
                  f"P(ahead) 10y {s10['p_profit']:.1%} median ${s10['median']:,.0f} | 30y {s30['p_profit']:.1%}")
