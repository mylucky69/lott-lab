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


def value(game: Game, draws: list[dict], jackpot: float | None, price: float | None) -> dict | None:
    if not price or not draws:
        return None
    p1 = game.division_odds()[0]["p"]
    entries = entries_estimate(game, draws)
    low = lower_division_ev(game, draws)
    out = {"price": price, "low_div_ev": round(low, 4), "entries": entries, "jackpot": jackpot}
    if jackpot and entries:
        ev = low + p1 * jackpot * _share(entries, p1)
        ev_busy = low + p1 * jackpot * _share(entries * 3, p1)
        out.update(ev_line=round(ev, 4), per_dollar=round(ev / price, 3),
                   per_dollar_busy=round(ev_busy / price, 3))
        # jackpot at which one line is worth its price (normal sales)
        lo, hi = 0.0, 5e9
        for _ in range(80):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if low + p1 * mid * _share(entries, p1) < price else (lo, mid)
        out["break_even_jackpot"] = round(hi, -5)
        r = out["per_dollar"]
        out["rating"] = ("positive" if r >= 1 else "high" if r >= 0.8 else "medium" if r >= 0.55 else "low")
    return out
