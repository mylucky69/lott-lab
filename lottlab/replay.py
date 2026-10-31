"""Replay the last N draws with a weekly budget:  python -m lottlab.replay [weeks] [budget]

For every past draw, build the lines exactly as the live system would have
(only data from before that draw), check them against the real result and add
the real prize paid for each division. Ticket prices come from config.json.
"""
from __future__ import annotations

import json
import sys

from .cli import CONFIG
from .games import GAMES, check_line
from .generator import generate
from .store import load_draws


def replay(weeks: int = 20, budget: float = 500.0, strategy: str = "balanced") -> dict:
    prices = CONFIG.get("ticket_price") or {}
    share = budget / len(GAMES)
    out = {"weeks": weeks, "budget_per_week": budget, "strategy": strategy, "games": {}}
    for game in GAMES.values():
        price = prices.get(game.id)
        if not price:
            raise SystemExit(f"Set ticket_price.{game.id} in config.json")
        n_lines = int(share // price)
        draws = load_draws(game)
        rows, cost, prize = [], 0.0, 0.0
        for i in range(len(draws) - weeks, len(draws)):
            d = draws[i]
            lines = generate(game, draws[:i], d["draw"], strategy, n_lines)
            amounts = {x["div"]: float(x["amount"] or 0) for x in d.get("dividends", [])}
            wins, won = {}, 0.0
            for line in lines:
                div = check_line(game, line, d)["div"]
                if div:
                    wins[div] = wins.get(div, 0) + 1
                    won += amounts.get(div, 0.0)
            c = n_lines * price
            cost += c
            prize += won
            rows.append({"draw": d["draw"], "date": d["date"], "cost": round(c, 2), "won": round(won, 2),
                         "wins": dict(sorted(wins.items())), "dividends_known": bool(amounts)})
        out["games"][game.id] = {"name": game.name, "price": price, "lines_per_draw": n_lines,
                                 "cost": round(cost, 2), "prize": round(prize, 2),
                                 "net": round(prize - cost, 2), "draws": rows}
    out["cost"] = round(sum(g["cost"] for g in out["games"].values()), 2)
    out["prize"] = round(sum(g["prize"] for g in out["games"].values()), 2)
    out["net"] = round(out["prize"] - out["cost"], 2)
    return out


if __name__ == "__main__":
    weeks = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    budget = float(sys.argv[2]) if len(sys.argv) > 2 else 500.0
    strategy = sys.argv[3] if len(sys.argv) > 3 else "balanced"
    print(json.dumps(replay(weeks, budget, strategy), ensure_ascii=False, indent=1))
