"""Builds the 50 lines for a draw.

Important: no strategy can change the chance that a line wins - every combination
is equally likely. What this generator CAN do:
  * coverage  - spread the 50 lines over the whole pool so they overlap as little
                as possible (more distinct combinations of numbers covered, more
                even results, every Powerball number covered for Powerball);
  * anti-popular - avoid combinations many people play (birthdays 1-31, runs,
                patterns, last week's numbers). Same odds, but if it wins the
                prize is shared with fewer people.
The 'hot' / 'cold' strategies exist for the backtest experiment.
"""
from __future__ import annotations

import zlib
from collections import Counter

import numpy as np

from .games import Game

STRATEGIES = {
    "balanced": "Phủ đều + tránh bộ số phổ biến (khuyến nghị)",
    "hot": "Ưu tiên số ra nhiều trong 30 kỳ gần nhất",
    "cold": "Ưu tiên số lâu chưa ra / ra ít",
    "random": "Ngẫu nhiên hoàn toàn (mốc so sánh)",
}


def seed_for(game: Game, draw: int, strategy: str) -> int:
    return zlib.crc32(f"{game.id}:{draw}:{strategy}".encode())


def number_weights(game: Game, history: list[dict], strategy: str, window: int = 30) -> np.ndarray:
    w = np.ones(game.pool + 1)
    w[0] = 0
    if strategy in ("hot", "cold") and history:
        recent = history[-window:]
        c = Counter(n for d in recent for n in d["main"])
        freq = np.array([0] + [c.get(n, 0) for n in range(1, game.pool + 1)], dtype=float)
        if strategy == "hot":
            w[1:] = 1 + 2.0 * freq[1:] / max(freq.max(), 1)
        else:
            last_seen = np.full(game.pool + 1, len(history), dtype=float)
            for i, d in enumerate(reversed(history)):
                for n in d["main"]:
                    last_seen[n] = min(last_seen[n], i)
            w[1:] = 1 + 2.0 * last_seen[1:] / max(last_seen[1:].max(), 1)
    return w


POP_WEIGHT = 32.0  # weight of the measured number popularity (chosen in research: +value, keeps P(any prize) above random)


def popularity_penalty(game: Game, lines: np.ndarray, last_main: set[int], past: set[tuple],
                       pop_weight: float | None = None) -> np.ndarray:
    """Higher = more people likely play this combination (so prizes are shared more).
    lines: (C, pick) sorted. Uses the popularity measured from real winner counts when
    available; otherwise falls back to simple rules (birthday numbers)."""
    from .popularity import line_popularity, model
    w = POP_WEIGHT if pop_weight is None else pop_weight
    pen = np.zeros(len(lines))
    diffs = np.diff(lines, axis=1)
    if model() and w > 0:
        pen += w * line_popularity(lines, game.pool)
    else:
        low = (lines <= 31).sum(1)
        pen += np.where(low == game.pick, 4.0, np.where(low == game.pick - 1, 1.5, 0.0))
    # well-known slip patterns (rare, but heavily played when they occur)
    pen += (diffs == diffs[:, :1]).all(1) * 10.0      # arithmetic sequences 5,10,15,...
    pen += ((lines % 5 == 0).all(1) | (lines % 7 == 0).all(1)) * 5.0
    run4 = ((diffs[:, :-2] == 1) & (diffs[:, 1:-1] == 1) & (diffs[:, 2:] == 1)).any(1)
    pen += run4 * 5.0                                  # 4+ in a row, e.g. 1,2,3,4
    if last_main:                                      # replaying last draw's numbers
        rep = np.isin(lines, list(last_main)).sum(1)
        pen += np.maximum(rep - 2, 0) * 2.0
    if past:
        pen += np.array([tuple(r) in past for r in lines.tolist()]) * 100.0
    return pen


def _sample(rng, logits: np.ndarray, size: int, k: int) -> np.ndarray:
    """Gumbel top-k: `size` candidate lines of k distinct numbers, sorted."""
    g = rng.gumbel(size=(size, len(logits)))
    scores = logits[None, :] + g
    idx = np.argpartition(-scores, k, axis=1)[:, :k]
    return np.sort(idx, axis=1)


def generate(game: Game, history: list[dict], draw: int, strategy: str = "balanced",
             n_lines: int = 50, candidates: int = 3000, polish: int = 400,
             pop_weight: float | None = None) -> list[dict]:
    rng = np.random.default_rng(seed_for(game, draw, strategy))
    P, k = game.pool, game.pick

    if strategy == "random":
        lines = [sorted(rng.choice(np.arange(1, P + 1), k, replace=False).tolist()) for _ in range(n_lines)]
        pbs = rng.integers(1, game.pb_pool + 1, n_lines).tolist() if game.pb_pool else [None] * n_lines
        return [_line(l, pb) for l, pb in zip(lines, pbs)]

    w = number_weights(game, history, strategy)
    base_logits = np.log(np.maximum(w, 1e-9))
    base_logits[0] = -1e9
    from .popularity import effects
    pw = POP_WEIGHT if pop_weight is None else pop_weight
    e, _ = effects(P)
    base_logits = base_logits - pw * 0.6 * e         # sample less popular numbers more often
    last_main = set(history[-1]["main"]) if history else set()
    past = {tuple(sorted(d["main"])) for d in history} if k == game.drawn else set()

    usage = np.zeros(P + 1)
    target = n_lines * k / P
    chosen = np.zeros((0, P + 1))
    lines: list[list[int]] = []
    for i in range(n_lines):
        logits = base_logits - 1.2 * usage / max(target, 1)
        cand = _sample(rng, logits, candidates, k)
        onehot = np.zeros((len(cand), P + 1))
        np.put_along_axis(onehot, cand, 1, axis=1)
        if len(chosen):
            ov = onehot @ chosen.T
            ov_pen = (np.maximum(ov - 1, 0) ** 2).sum(1) + 5 * (ov >= k - 2).sum(1)
        else:
            ov_pen = np.zeros(len(cand))
        use_pen = usage[cand].sum(1) / max(target, 1)
        pref = (np.log(w[cand])).sum(1)
        score = ov_pen * 1.0 + use_pen * 0.8 + popularity_penalty(game, cand, last_main, past, pw) - pref
        best = int(np.argmin(score))
        lines.append(cand[best].tolist())
        chosen = np.vstack([chosen, onehot[best]])
        usage[cand[best]] += 1

    lines = _polish(game, rng, lines, w, last_main, past, polish, pw)
    if game.pb_pool:
        pbs = _powerballs(game, rng, history, strategy, n_lines)
    else:
        pbs = [None] * n_lines
    return [_line(l, pb) for l, pb in zip(lines, pbs)]


def _set_score(game, lines_arr, w, last_main, past, pw=None) -> float:
    P = game.pool
    onehot = np.zeros((len(lines_arr), P + 1))
    np.put_along_axis(onehot, lines_arr, 1, axis=1)
    ov = onehot @ onehot.T
    np.fill_diagonal(ov, 0)
    ov_pen = (np.maximum(ov - 1, 0) ** 2).sum() / 2
    usage = onehot.sum(0)[1:]
    use_pen = ((usage - usage.mean()) ** 2).sum()
    pop = popularity_penalty(game, lines_arr, last_main, past, pw).sum()
    pref = np.log(w[lines_arr]).sum()
    return ov_pen + 0.5 * use_pen + pop - pref


def _polish(game, rng, lines, w, last_main, past, iters, pw=None):
    """Local search: swap single numbers when it improves the whole set."""
    arr = np.array(lines)
    best = _set_score(game, arr, w, last_main, past, pw)
    for _ in range(iters):
        i, j = rng.integers(len(arr)), rng.integers(game.pick)
        new = rng.integers(1, game.pool + 1)
        if new in arr[i]:
            continue
        trial = arr.copy()
        trial[i, j] = new
        trial[i].sort()
        s = _set_score(game, trial, w, last_main, past, pw)
        if s < best:
            arr, best = trial, s
    return sorted(arr.tolist())


def _powerballs(game, rng, history, strategy, n):
    """Spread Powerball numbers so all 1..20 are covered (each used 2-3 times for 50 lines)."""
    pool = np.arange(1, game.pb_pool + 1)
    if strategy in ("hot", "cold") and history:
        c = Counter(d["supp"][0] for d in history[-30:] if d.get("supp"))
        freq = np.array([c.get(int(p), 0) for p in pool], dtype=float)
        order = pool[np.argsort(-freq if strategy == "hot" else freq, kind="stable")]
    else:
        order = rng.permutation(pool)
    seq = np.resize(order, n)
    return rng.permutation(seq).tolist()


def _line(main, pb):
    d = {"main": [int(x) for x in sorted(main)]}
    if pb is not None:
        d["pb"] = int(pb)
    return d
