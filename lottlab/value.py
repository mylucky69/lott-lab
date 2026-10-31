"""Expected value of one line for the next draw, from the announced jackpot.

  EV(line) = sum over lower divisions  P(div) x typical payout   (median of recent draws)
           + P(div 1) x jackpot x expected share

The jackpot is split between all division-1 winners. With N lines sold, the number
of OTHER winners is ~Poisson(N x P1), so the expected share of one winning line is
(1 - e^-l) / l  with  l = N x P1.  N is estimated from the real number of winners in
the lower divisions (winners / probability), which the prize tables give us.
Big jackpots attract more players, so we also show a 'busy draw' figure (3x sales).
"""
from __future__ import annotations

import math
import statistics

from .games import Game


def entries_estimate(game: Game, draws: list[dict], recent: int = 10) -> float | None:
    odds = {o["division"]: o["p"] for o in game.division_odds()}
    est = []
    for d in [d for d in draws if d.get("dividends")][-recent:]:
        for x in d["dividends"]:
            if x["div"] >= 3 and (x.get("winners") or 0) >= 50 and x["div"] in odds:
                est.append(x["winners"] / odds[x["div"]])
    return statistics.median(est) if est else None


def lower_division_ev(game: Game, draws: list[dict], recent: int = 30) -> float:
    ev = 0.0
    recent_draws = [d for d in draws if d.get("dividends")][-recent:]
    for o in game.division_odds()[1:]:
        amounts = [x["amount"] for d in recent_draws for x in d["dividends"]
                   if x["div"] == o["division"] and x.get("amount")]
        if amounts:
            ev += o["p"] * statistics.median(amounts)
    return ev


def _share(entries: float, p1: float) -> float:
    lam = entries * p1
    return (1 - math.exp(-lam)) / lam if lam > 1e-12 else 1.0


def value(game: Game, draws: list[dict], jackpot: float | None, price: float | None,
          lines: list[dict] | None = None) -> dict | None:
    if not price or not draws:
        return None
    p1 = game.division_odds()[0]["p"]
    entries = entries_estimate(game, draws)
    low = lower_division_ev(game, draws)
    out = {"price": price, "low_div_ev": round(low, 4), "entries": entries, "jackpot": jackpot}
    if jackpot and entries:
        # sales grow with the jackpot: use the model fitted on real draws (optimize.py) when present
        from .store import DATA, read_json
        sm = (read_json(DATA / game.id / "optimize.json", {}) or {}).get("sales_model")

        def sales_at(j):
            return max(sm["a"] + sm["b"] * j, entries * 0.5) if sm else entries * 3
        ev = low + p1 * jackpot * _share(entries, p1)                 # if sales stay as now
        ev_busy = low + p1 * jackpot * _share(sales_at(jackpot), p1)  # sales expected at this jackpot
        ratio = 1.0
        if lines:
            from .popularity import set_value_exact
            recent = [d for d in draws if d.get("dividends")][-30:]
            pay = {o["division"]: statistics.median([x["amount"] for d in recent for x in d["dividends"]
                                                     if x["div"] == o["division"] and x.get("amount")] or [0])
                   for o in game.division_odds()}
            sv = set_value_exact(game, lines, pay, sales_at(jackpot), jackpot)
            ratio = sv["value_set"] / sv["value_avg"] if sv["value_avg"] else 1.0
            out["set_ratio"] = round(ratio, 4)
        ev, ev_busy = ev * ratio, ev_busy * ratio
        out.update(ev_line=round(ev_busy, 4), per_dollar=round(max(ev, ev_busy) / price, 3),
                   per_dollar_busy=round(min(ev, ev_busy) / price, 3), expected_sales=round(sales_at(jackpot)))
        # jackpot at which one line is worth its price, with sales growing with the jackpot
        lo, hi = 0.0, 2e9
        for _ in range(80):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if (low + p1 * mid * _share(sales_at(mid), p1)) * ratio < price else (lo, mid)
        out["break_even_jackpot"] = round(hi, -6) if hi < 1.9e9 else None
        r = out["per_dollar_busy"]
        out["rating"] = ("positive" if r >= 1 else "high" if r >= 0.75 else "medium" if r >= 0.55 else "low")
    return out
