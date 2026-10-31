"""Statistics, randomness tests, Monte Carlo and walk-forward backtest."""
from __future__ import annotations

from collections import Counter
from math import comb

import numpy as np
from scipy import stats as sps

from .games import Game, check_line
from .generator import STRATEGIES, generate


# -- descriptive statistics + randomness tests -------------------------------
def statistics(game: Game, draws: list[dict], recent: int = 20) -> dict:
    n = len(draws)
    if n == 0:
        return {"n_draws": 0}
    P = game.pool
    c = Counter(x for d in draws for x in d["main"])
    freq = [c.get(i, 0) for i in range(1, P + 1)]
    expected = n * game.drawn / P
    chi2, p_chi = sps.chisquare(freq)

    rc = Counter(x for d in draws[-recent:] for x in d["main"])
    last_seen = {}
    for i, d in enumerate(reversed(draws)):
        for x in d["main"]:
            last_seen.setdefault(x, i)

    # independence: overlap between consecutive draws vs. hypergeometric expectation
    overlaps = [len(set(a["main"]) & set(b["main"])) for a, b in zip(draws, draws[1:])]
    exp_overlap = game.drawn * game.drawn / P
    if len(overlaps) > 5:
        t = sps.ttest_1samp(overlaps, exp_overlap)
        p_serial = float(t.pvalue)
    else:
        p_serial = None

    # odd count distribution vs. hypergeometric
    odd_n = (P + 1) // 2
    odd_obs = Counter(sum(x % 2 for x in d["main"]) for d in draws)
    odd = [{"odd": j, "observed": odd_obs.get(j, 0),
            "expected": n * comb(odd_n, j) * comb(P - odd_n, game.drawn - j) / comb(P, game.drawn)}
           for j in range(game.drawn + 1)]

    out = {
        "n_draws": n, "first": draws[0]["date"], "last": draws[-1]["date"],
        "numbers": [{"n": i, "count": freq[i - 1], "recent": rc.get(i, 0),
                     "gap": last_seen.get(i, n)} for i in range(1, P + 1)],
        "expected_count": expected,
        "chi2": float(chi2), "p_chi2": float(p_chi),
        "mean_overlap": float(np.mean(overlaps)) if overlaps else None,
        "expected_overlap": exp_overlap, "p_serial": p_serial,
        "odd": odd,
    }
    if game.pb_pool:
        pc = Counter(d["supp"][0] for d in draws if d.get("supp"))
        pf = [pc.get(i, 0) for i in range(1, game.pb_pool + 1)]
        out["pb"] = [{"n": i, "count": pf[i - 1]} for i in range(1, game.pb_pool + 1)]
        out["pb_expected"] = n / game.pb_pool
        out["p_chi2_pb"] = float(sps.chisquare(pf).pvalue)
    return out


# -- Monte Carlo: how does a 50-line set behave over many random draws ------
def _div_lookup(game: Game) -> np.ndarray:
    tbl = np.zeros((game.pick + 1, max(game.supps, 1) + 1, 2), dtype=int)
    for m in range(game.pick + 1):
        for s in range(tbl.shape[1]):
            for pb in (0, 1):
                tbl[m, s, pb] = game.division(m, s, bool(pb)) or 0
    return tbl


def simulate_set(game: Game, lines: list[dict], n: int = 20000, seed: int = 1) -> dict:
    rng = np.random.default_rng(seed)
    P = game.pool
    L = np.zeros((len(lines), P + 1))
    for i, l in enumerate(lines):
        L[i, l["main"]] = 1
    picks = np.argsort(rng.random((n, P)), axis=1)[:, :game.drawn + game.supps] + 1
    D = np.zeros((n, P + 1))
    np.put_along_axis(D, picks[:, :game.drawn], 1, axis=1)
    M = (D @ L.T).astype(int)
    if game.supps:
        S = np.zeros((n, P + 1))
        np.put_along_axis(S, picks[:, game.drawn:], 1, axis=1)
        Sm = (S @ L.T).astype(int)
    else:
        Sm = np.zeros_like(M)
    if game.pb_pool:
        pb_draw = rng.integers(1, game.pb_pool + 1, n)
        pbl = np.array([l["pb"] for l in lines])
        PB = (pb_draw[:, None] == pbl[None, :]).astype(int)
    else:
        PB = np.zeros_like(M)
    div = _div_lookup(game)[M, Sm, PB]
    won = div > 0
    return {"draws": n, "p_any": float(won.any(1).mean()),
            "mean_prizes": float(won.sum(1).mean()),
            "p_any_independent": 1 - (1 - game.any_prize_p()) ** len(lines),
            "mean_prizes_theory": game.any_prize_p() * len(lines)}


# -- walk-forward backtest ---------------------------------------------------
def backtest(game: Game, draws: list[dict], last_n: int = 150, n_lines: int = 50,
             min_history: int = 30) -> dict:
    """For each of the last `last_n` draws, build lines using only earlier draws."""
    start = max(min_history, len(draws) - last_n)
    tested = draws[start:]
    res = {}
    for strat in STRATEGIES:
        divs = Counter()
        prize, prize_known, matches, lines_total = 0.0, 0, 0, 0
        for idx, d in enumerate(tested, start):
            lines = generate(game, draws[:idx], d["draw"], strat, n_lines, candidates=300, polish=0)
            amounts = {x["div"]: x.get("amount") for x in d.get("dividends", [])}
            for l in lines:
                r = check_line(game, l, d)
                matches += r["m"]
                lines_total += 1
                if r["div"]:
                    divs[r["div"]] += 1
                    a = amounts.get(r["div"])
                    if a is not None:
                        prize += float(a)
                        prize_known += 1
        res[strat] = {"label": STRATEGIES[strat], "draws": len(tested), "lines": lines_total,
                      "mean_match": matches / max(lines_total, 1),
                      "divisions": {str(k): v for k, v in sorted(divs.items())},
                      "prize_lines": sum(divs.values()), "prize_total": round(prize, 2)}
    exp_match = game.pick * game.drawn / game.pool
    lines_total = len(tested) * n_lines
    theory = {"mean_match": exp_match, "prize_lines": game.any_prize_p() * lines_total,
              "divisions": {str(o["division"]): o["p"] * lines_total for o in game.division_odds()}}
    # does any strategy differ from chance? (binomial test on number of prize lines)
    for strat, r in res.items():
        r["p_value"] = float(sps.binomtest(r["prize_lines"], lines_total, game.any_prize_p()).pvalue) \
            if lines_total else None
    return {"strategies": res, "theory": theory, "draws": len(tested),
            "from": tested[0]["date"] if tested else None, "to": tested[-1]["date"] if tested else None}
