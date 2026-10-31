"""Find the jackpot threshold per game with the best money outcome.   python -m lottlab.optimize

1. Sales model: lines sold = a + b x jackpot, fitted on real draws (sales come from
   the real number of lower-division winners).
2. Value curve: value of one line per $1 for every jackpot from $3M to $200M,
   using that sales model (more sales = more people to share the jackpot with).
3. Threshold sweep over the real draws (Jan 2025 on): for every threshold, the
   number of draws played per year, average value per $1, expected loss per year,
   and the real result of replaying the 50 lines the system would have locked.
"""
from __future__ import annotations

import math
import statistics
import sys

import numpy as np

from .cli import CONFIG, N_LINES
from .games import GAMES, Game, check_line
from .generator import generate
from .store import DATA, load_draws, write_json
from .study import draw_sales

GRID = [3e6, 5e6, 8e6, 10e6, 15e6, 20e6, 25e6, 30e6, 40e6, 50e6, 60e6, 70e6, 80e6, 100e6,
        120e6, 150e6, 200e6]


def _share(lam: float) -> float:
    return (1 - math.exp(-lam)) / lam if lam > 1e-12 else 1.0


def per_draw(game: Game, draws: list[dict], price: float) -> list[dict]:
    """Value per $1 and real replay result for every draw with a known jackpot."""
    odds = game.division_odds()
    p1 = odds[0]["p"]
    rows = []
    for i, d in enumerate(draws):
        if not d.get("jackpot") or not d.get("dividends"):
            continue
        sales = draw_sales(game, d)
        if not sales:
            continue
        paid = {x["div"]: float(x.get("amount") or 0) for x in d["dividends"]}
        low = sum(o["p"] * paid.get(o["division"], 0.0) for o in odds[1:])
        value = low + p1 * d["jackpot"] * _share(sales * p1)
        lines = generate(game, draws[:i], d["draw"], "balanced", N_LINES, candidates=1200, polish=150)
        won = 0.0
        for line in lines:
            div = check_line(game, line, d)["div"]
            if div and div != 1:
                won += paid.get(div, 0.0)
        rows.append({"draw": d["draw"], "date": d["date"], "jackpot": d["jackpot"], "sales": sales,
                     "low": low, "per_dollar": value / price, "cost": N_LINES * price, "won": won})
    return rows


def optimise(game: Game, price: float) -> dict:
    rows = per_draw(game, load_draws(game), price)
    if len(rows) < 10:
        return {"game": game.id, "draws": len(rows)}
    J = np.array([r["jackpot"] for r in rows])
    S = np.array([r["sales"] for r in rows])
    b, a = np.polyfit(J, S, 1)
    r2 = 1 - ((S - (a + b * J)) ** 2).sum() / ((S - S.mean()) ** 2).sum()
    low = statistics.median(r["low"] for r in rows)
    p1 = game.division_odds()[0]["p"]
    curve = []
    for j in GRID:
        s = max(a + b * j, S.min())
        v = (low + p1 * j * _share(s * p1)) / price
        curve.append({"jackpot": j, "sales": round(s), "per_dollar": round(v, 3)})
    best_curve = max(curve, key=lambda c: c["per_dollar"])

    years = (np.datetime64(rows[-1]["date"]) - np.datetime64(rows[0]["date"])).astype(int) / 365.25
    years = max(years, 0.5)
    sweep = []
    for t in sorted(set(J)):
        q = [r for r in rows if r["jackpot"] >= t]
        n = len(q)
        spend = sum(r["cost"] for r in q)
        exp_return = sum(r["per_dollar"] * r["cost"] for r in q)
        won = sum(r["won"] for r in q)
        sweep.append({
            "threshold": float(t), "draws": n, "per_year": round(n / years, 1),
            "per_dollar": round(exp_return / spend, 3),
            "spend_per_year": round(spend / years, 0),
            "expected_loss_per_year": round((spend - exp_return) / years, 0),
            "real_net": round(won - spend, 2), "real_net_per_year": round((won - spend) / years, 0),
        })
    # recommendation: best value per $1 among thresholds that still play >= 4 draws a year
    playable = [s for s in sweep if s["per_year"] >= 4]
    rec = max(playable, key=lambda s: (s["per_dollar"], -s["threshold"])) if playable else None
    return {"game": game.id, "price": price, "draws": len(rows), "years": round(float(years), 2),
            "sales_model": {"a": a, "b": b, "r2": round(float(r2), 3)},
            "low_div_value_per_line": round(low, 4), "curve": curve, "curve_best": best_curve,
            "break_even": next((c["jackpot"] for c in curve if c["per_dollar"] >= 1), None),
            "sweep": sweep, "recommended": rec}


if __name__ == "__main__":
    prices = CONFIG.get("ticket_price") or {}
    for gid in sys.argv[1:] or list(GAMES):
        g = GAMES[gid]
        res = optimise(g, prices[gid])
        write_json(DATA / g.id / "optimize.json", res)
        if "sweep" not in res:
            print(f"[{g.name}] not enough data"); continue
        m = res["sales_model"]
        print(f"\n[{g.name}] {res['draws']} draws over {res['years']} years, price ${res['price']}")
        print(f"  sales ≈ {m['a']/1e6:.1f}M + {m['b']:.2f} × jackpot  (R²={m['r2']})  | lower divisions ${res['low_div_value_per_line']:.3f}/line")
        print("  value per $1 by jackpot: " + ", ".join(f"${c['jackpot']/1e6:.0f}M {c['per_dollar']:.2f}" for c in res["curve"]))
        print(f"  best on curve: ${res['curve_best']['jackpot']/1e6:.0f}M → {res['curve_best']['per_dollar']:.2f} | break-even: {res['break_even']}")
        print(f"  {'threshold':>10} {'draws':>5} {'/yr':>5} {'$/1':>5} {'spend/yr':>9} {'exp loss/yr':>11} {'real net':>9} {'real/yr':>8}")
        for s in res["sweep"]:
            print(f"  ${s['threshold']/1e6:>8.0f}M {s['draws']:>5} {s['per_year']:>5} {s['per_dollar']:>5.2f} "
                  f"{s['spend_per_year']:>9,.0f} {s['expected_loss_per_year']:>11,.0f} {s['real_net']:>9,.0f} {s['real_net_per_year']:>8,.0f}")
        r = res["recommended"]
        if r:
            print(f"  → recommended: ≥ ${r['threshold']/1e6:.0f}M ({r['per_year']} draws/yr, ${r['per_dollar']:.2f} per $1, "
                  f"expected loss ${r['expected_loss_per_year']:,.0f}/yr)")
