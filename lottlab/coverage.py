"""Raise the chance that at least one line wins a prize, for a given number of lines.

Each line wins a prize with the same probability p (1/50.3 for Oz Lotto), so with n lines
the chance of at least one prize can never exceed n x p. It falls below that when lines
win on the same draws (they overlap). This optimiser searches line sets directly for the
fewest shared wins: it scores a set on a large sample of random draws ("training") and
reports the result on a separate sample it never saw ("check"), so the gain is real and
not tuned to one sample.
"""
from __future__ import annotations

import numpy as np

from .analysis import _div_lookup
from .games import Game


def _draw_sample(game: Game, n: int, seed: int):
    rng = np.random.default_rng(seed)
    P, K = game.pool, game.drawn
    picks = np.argsort(rng.random((n, P)), axis=1)[:, :K + game.supps] + 1
    D = np.zeros((n, P + 1), dtype=np.int8)
    np.put_along_axis(D, picks[:, :K], 1, axis=1)
    S = np.zeros((n, P + 1), dtype=np.int8)
    if game.supps:
        np.put_along_axis(S, picks[:, K:], 1, axis=1)
    pb = rng.integers(1, game.pb_pool + 1, n) if game.pb_pool else None
    return D, S, pb


def _wins(game: Game, table, D, S, pbd, onehot, pb):
    """Boolean (draws, lines): line wins any prize on that draw."""
    m = D @ onehot.T
    s = S @ onehot.T if game.supps else np.zeros_like(m)
    if game.pb_pool:
        hit = (pbd[:, None] == np.asarray(pb)[None, :]).astype(int)
    else:
        hit = np.zeros_like(m)
    return table[m, s, hit] > 0


def p_any(game: Game, lines: list[dict], n: int = 200000, seed: int = 99) -> float:
    D, S, pbd = _draw_sample(game, n, seed)
    oh = np.zeros((len(lines), game.pool + 1), dtype=np.int8)
    for i, l in enumerate(lines):
        oh[i, l["main"]] = 1
    pb = [l.get("pb", 0) for l in lines]
    return float(_wins(game, _div_lookup(game), D, S, pbd, oh, pb).any(1).mean())


def optimise(game: Game, lines: list[dict], iters: int = 4000, sample: int = 60000, seed: int = 5,
             pop_weight: float = 0.0) -> list[dict]:
    """Local search: change one number of one line at a time, keep the change when more
    draws are covered (fewer draws on which nobody wins), optionally also rewarding less
    popular numbers. Returns the improved set."""
    from .popularity import effects
    rng = np.random.default_rng(seed)
    table = _div_lookup(game)
    D, S, pbd = _draw_sample(game, sample, seed + 1)
    P = game.pool
    e, _ = effects(P)
    L = [list(l["main"]) for l in lines]
    pb = [l.get("pb", 0) for l in lines]
    oh = np.zeros((len(L), P + 1), dtype=np.int8)
    for i, l in enumerate(L):
        oh[i, l] = 1
    W = _wins(game, table, D, S, pbd, oh, pb)              # (draws, lines)
    cover = W.sum(1)
    covered = int((cover > 0).sum())
    pop = np.array([e[l].sum() for l in L])
    existing = {tuple(sorted(l)) for l in L}

    def score(cov, popsum):
        return cov - pop_weight * popsum * sample / 1000.0

    best = score(covered, pop.sum())
    for _ in range(iters):
        i = int(rng.integers(len(L)))
        j = int(rng.integers(game.pick))
        new = int(rng.integers(1, P + 1))
        if new in L[i]:
            continue
        cand = sorted(L[i][:j] + L[i][j + 1:] + [new])
        if tuple(cand) in existing:
            continue
        row = np.zeros(P + 1, dtype=np.int8)
        row[cand] = 1
        w_new = _wins(game, table, D, S, pbd, row[None, :], [pb[i]])[:, 0]
        cover_new = cover - W[:, i] + w_new
        cov_new = int((cover_new > 0).sum())
        pop_new = pop.sum() - pop[i] + e[cand].sum()
        s_new = score(cov_new, pop_new)
        if s_new > best:
            existing.discard(tuple(sorted(L[i])))
            existing.add(tuple(cand))
            L[i], W[:, i], cover, pop[i], best = cand, w_new, cover_new, e[cand].sum(), s_new
        # Powerball: also try moving the PB of a line
        if game.pb_pool and rng.random() < 0.2:
            k = int(rng.integers(len(L)))
            newpb = int(rng.integers(1, game.pb_pool + 1))
            if newpb != pb[k]:
                row = np.zeros(P + 1, dtype=np.int8)
                row[L[k]] = 1
                w2 = _wins(game, table, D, S, pbd, row[None, :], [newpb])[:, 0]
                c2 = cover - W[:, k] + w2
                s2 = score(int((c2 > 0).sum()), pop.sum())
                if s2 > best:
                    pb[k], W[:, k], cover, best = newpb, w2, c2, s2
    out = []
    for l, b in zip(L, pb):
        d = {"main": sorted(int(x) for x in l)}
        if game.pb_pool:
            d["pb"] = int(b)
        out.append(d)
    return sorted(out, key=lambda d: d["main"])


def prize_stats(game: Game, lines: list[dict], payouts: dict[int, float], price: float,
                n: int = 60000, seed: int = 11) -> dict:
    """Per draw, ignoring the jackpot: chance of at least one prize, chance that the prizes
    won pay back the whole ticket cost, and the average amount won."""
    D, S, pbd = _draw_sample(game, n, seed)
    oh = np.zeros((len(lines), game.pool + 1), dtype=np.int8)
    for i, l in enumerate(lines):
        oh[i, l["main"]] = 1
    pb = [l.get("pb", 0) for l in lines]
    m = D @ oh.T
    s = S @ oh.T if game.supps else np.zeros_like(m)
    hit = (pbd[:, None] == np.asarray(pb)[None, :]).astype(int) if game.pb_pool else np.zeros_like(m)
    div = _div_lookup(game)[m, s, hit]
    pay = np.zeros(game.n_divisions + 1)
    for d, a in payouts.items():
        if 2 <= d <= game.n_divisions:
            pay[d] = a
    total = pay[div].sum(1)
    cost = len(lines) * price
    return {"p_any": float((div > 0).any(1).mean()), "p_recoup": float((total >= cost).mean()),
            "mean_return": float(total.mean())}
