"""Which numbers do Australian players pick most?   python -m lottlab.popularity

Prizes in every division are shared between winners. When the drawn numbers are
popular, more people win and each gets less. So the number of winners in a draw,
compared with the draw's sales, measures how popular the drawn numbers are.

Model (fitted on Saturday Lotto, the game with the most draws and stable sales):
    log(winners / expected winners) = (k/6) x sum(effect of each drawn number)
                                      + effect x (consecutive pairs) + division constant
where k = numbers that must match for that division. Sales come from the median of
the neighbouring draws (the draw itself is left out); draws whose sales jump
(Superdraws) are skipped. A ridge penalty keeps per-number effects stable.
"""
from __future__ import annotations

import statistics

import numpy as np

from .games import GAMES
from .store import DATA, load_draws, read_json, write_json
from .study import draw_sales

PATH = DATA / "popularity.json"
_cache: dict | None = None


def fit(ridge: float = 2.0) -> dict:
    g = GAMES["saturday"]
    odds = {o["division"]: o["p"] for o in g.division_odds()}
    need = {2: 5, 3: 5, 4: 4, 5: 3}
    draws = [d for d in load_draws(g) if d.get("dividends")]
    sales = [draw_sales(g, d) for d in draws]
    X, y = [], []
    for i, d in enumerate(draws):
        nb = [sales[j] for j in range(max(0, i - 5), min(len(draws), i + 6)) if j != i and sales[j]]
        if len(nb) < 8:
            continue
        base = statistics.median(nb)
        if sales[i] and not 0.75 < sales[i] / base < 1.33:
            continue
        row = np.zeros(g.pool)
        row[np.array(d["main"]) - 1] = 1
        consec = int((np.diff(sorted(d["main"])) == 1).sum())
        for div, k in need.items():
            w = next((x["winners"] for x in d["dividends"] if x["div"] == div), None)
            if not w or w < 20:
                continue
            X.append(np.concatenate([row * k / 6, [consec], [div == v for v in (2, 3, 4)]]))
            y.append(np.log(w / (base * odds[div])))
    X, y = np.array(X, float), np.array(y)
    X1 = np.column_stack([np.ones(len(X)), X])
    pen = np.diag([0] + [ridge] * g.pool + [0] * 4)
    b = np.linalg.solve(X1.T @ X1 + pen, X1.T @ y)
    resid = y - X1 @ b
    beta = b[1:1 + g.pool] - b[1:1 + g.pool].mean()
    per = {str(n): float(beta[n - 1]) for n in range(1, g.pool + 1)}
    high = float(np.mean(beta[40:]))         # numbers 41-45 stand in for 46-47 (Oz Lotto)
    per.update({"46": high, "47": high})
    model = {"per_number": per, "consecutive": float(b[1 + g.pool]), "rows": int(len(y)),
             "draws_used": int(len(y) // 4), "fitted_on": "Saturday Lotto",
             "r2": round(float(1 - resid @ resid / ((y - y.mean()) @ (y - y.mean()))), 3)}
    write_json(PATH, model)
    global _cache
    _cache = model
    return model


def model() -> dict | None:
    global _cache
    if _cache is None:
        _cache = read_json(PATH)
    return _cache


def effects(pool: int) -> tuple[np.ndarray, float]:
    """Array e[n] (index 0 unused) of log-popularity per number, and the consecutive-pair effect."""
    m = model()
    e = np.zeros(pool + 1)
    if not m:
        return e, 0.0
    for n in range(1, pool + 1):
        e[n] = m["per_number"].get(str(n), 0.0)
    return e, m["consecutive"]


def line_popularity(lines: np.ndarray, pool: int, scale: float = 1.0) -> np.ndarray:
    """log popularity of each line (rows of sorted numbers). 0 = average line."""
    e, c = effects(pool)
    consec = (np.diff(lines, axis=1) == 1).sum(1)
    return scale * e[lines].sum(1) + c * consec


if __name__ == "__main__":
    m = fit()
    per = sorted(((float(v), int(k)) for k, v in m["per_number"].items() if int(k) <= 45), reverse=True)
    print(f"fitted on {m['draws_used']} Saturday Lotto draws ({m['rows']} division rows), R²={m['r2']}")
    print("most picked :", ", ".join(f"{n} (x{np.exp(v):.2f})" for v, n in per[:10]))
    print("least picked:", ", ".join(f"{n} (x{np.exp(v):.2f})" for v, n in per[-10:]))
    print(f"each consecutive pair: x{np.exp(m['consecutive']):.3f}")


# main numbers each division needs (used to scale the popularity effect)
NEED = {"oz": {1: 7, 2: 6, 3: 6, 4: 5, 5: 5, 6: 4, 7: 3},
        "saturday": {1: 6, 2: 5, 3: 5, 4: 4, 5: 3, 6: 3},
        "powerball": {1: 7, 2: 7, 3: 6, 4: 6, 5: 5, 6: 4, 7: 5, 8: 3, 9: 2}}


def set_value(game, lines: list[dict], payouts: dict[int, float], sales: float, jackpot: float,
              sims: int = 30000, seed: int = 3) -> dict:
    """Expected value of one line of this set, counting that prizes are shared:
    lower divisions pay more when the drawn numbers are unpopular, and the jackpot is
    split with fewer people when the line itself is unpopular."""
    import math
    from .games import check_line  # noqa: F401  (rules are applied via game.division)
    rng = np.random.default_rng(seed)
    P, K = game.pool, game.drawn
    e, c = effects(P)
    L = np.array([l["main"] for l in lines])
    onehot = np.zeros((len(L), P + 1))
    np.put_along_axis(onehot, L, 1, axis=1)
    picks = np.argsort(rng.random((sims, P)), axis=1)[:, :K + game.supps] + 1
    main = np.sort(picks[:, :K], axis=1)
    D = np.zeros((sims, P + 1)); np.put_along_axis(D, main, 1, axis=1)
    M = (D @ onehot.T).astype(int)
    if game.supps:
        S = np.zeros((sims, P + 1)); np.put_along_axis(S, picks[:, K:], 1, axis=1)
        Sm = (S @ onehot.T).astype(int)
    else:
        Sm = np.zeros_like(M)
    if game.pb_pool:
        pbd = rng.integers(1, game.pb_pool + 1, sims)
        PB = (pbd[:, None] == np.array([l["pb"] for l in lines])[None, :])
    else:
        PB = np.zeros_like(M, dtype=bool)
    pop_draw = e[main].sum(1) + c * (np.diff(main, axis=1) == 1).sum(1)
    need = NEED[game.id]
    plain = boosted = 0.0
    for div, k in need.items():
        if div == 1:
            continue
        hit = np.zeros_like(M, dtype=bool)
        for m in range(game.pick + 1):
            for s in range(max(game.supps, 1) + 1 if game.supps else 1):
                for pb in ((True, False) if game.pb_pool else (False,)):
                    if game.division(m, s, pb) == div:
                        hit |= (M == m) & (Sm == s) & (PB == pb)
        mult = np.exp(-(k / K) * pop_draw)
        mult /= mult.mean()                           # average draw = typical payout
        wins = hit.sum(1)
        plain += payouts.get(div, 0.0) * wins.sum()
        boosted += payouts.get(div, 0.0) * (wins * mult).sum()
    n = sims * len(lines)
    low_plain, low_boost = plain / n, boosted / n
    # jackpot share: co-winners scale with how popular the line is (vs. an average line)
    rand = np.sort(np.argsort(rng.random((20000, P)), axis=1)[:, :game.pick] + 1, axis=1)
    norm = np.exp(e[rand].sum(1) + c * (np.diff(rand, axis=1) == 1).sum(1)).mean()
    rel = np.exp(e[L].sum(1) + c * (np.diff(L, axis=1) == 1).sum(1)) / norm
    p1 = game.division_odds()[0]["p"]
    lam = sales * p1

    def share(l):
        return (1 - math.exp(-l)) / l if l > 1e-12 else 1.0
    jp_avg = p1 * jackpot * share(lam)
    jp_set = p1 * jackpot * float(np.mean([share(lam * r) for r in rel]))
    return {"low_plain": low_plain, "low_set": low_boost, "jackpot_avg": jp_avg, "jackpot_set": jp_set,
            "line_pop": float(np.mean(rel)),
            "value_avg": low_plain + jp_avg, "value_set": low_boost + jp_set}


def set_value_exact(game, lines: list[dict], payouts: dict[int, float], sales: float,
                    jackpot: float) -> dict:
    """Same as set_value, computed directly (no simulation noise).

    If a line wins a division by matching m of its numbers, the drawn set holds those
    m numbers plus K-m others; the payout scales with exp(-(k/K) x popularity of the drawn
    set). Averaging exp() over the matched and the unmatched numbers separately gives the
    payout multiplier for that line and division."""
    import math
    P, K = game.pool, game.drawn
    e, _ = effects(P)
    odds = {o["division"]: o["p"] for o in game.division_odds()}
    need = NEED[game.id]
    p1 = odds[1]
    lam = sales * p1
    allnums = np.arange(1, P + 1)
    low_plain = sum(odds[d] * payouts.get(d, 0.0) for d in need if d != 1)
    vals, rels = [], []
    for l in lines:
        mine = np.array(l["main"])
        other = np.setdiff1d(allnums, mine)
        low = 0.0
        for d, k in need.items():
            if d == 1:
                continue
            a = k / K
            base = np.exp(-a * e[allnums]).mean() ** K
            mult = (np.exp(-a * e[mine]).mean() ** k) * (np.exp(-a * e[other]).mean() ** (K - k)) / base
            low += odds[d] * payouts.get(d, 0.0) * mult
        rel = math.exp(e[mine].sum()) / (np.exp(e[allnums]).mean() ** game.pick)
        share = (1 - math.exp(-lam * rel)) / (lam * rel) if lam * rel > 1e-12 else 1.0
        vals.append(low + p1 * jackpot * share)
        rels.append(rel)
    share_avg = (1 - math.exp(-lam)) / lam if lam > 1e-12 else 1.0
    return {"value_avg": low_plain + p1 * jackpot * share_avg, "value_set": float(np.mean(vals)),
            "line_pop": float(np.mean(rels))}
