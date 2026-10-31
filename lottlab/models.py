"""Experiment: do the prediction methods used by popular GitHub lottery repos work?

python -m lottlab.models [game ...]

Every method scores each number 1..pool using ONLY draws before the one being
predicted (walk-forward). Two measures, each compared with pure chance:
  * top-k matches: how many of the method's top `pick` numbers were drawn
                   (chance = pick * drawn / pool)
  * rank score:    average percentile rank the method gave to the numbers that
                   were actually drawn (chance = 0.5; higher = better prediction)
"""
from __future__ import annotations

import json
import sys
import warnings

import numpy as np
from scipy import stats as sps

from .games import GAMES, Game
from .store import DATA, load_draws, write_json

warnings.filterwarnings("ignore")
WINDOW = 5          # draws of history fed to the ML models (like the LSTM repos)
RETRAIN_EVERY = 10  # retrain ML models every N draws (speed)


def onehot(game: Game, draws: list[dict]) -> np.ndarray:
    X = np.zeros((len(draws), game.pool))
    for i, d in enumerate(draws):
        X[i, np.array(d["main"]) - 1] = 1
    return X


# -- scoring methods (return array of length pool, higher = more likely) ------
def s_random(H, rng, **_):
    return rng.random(H.shape[1])


def s_frequency(H, **_):
    return H.sum(0)


def s_hot(H, **_):
    return H[-10:].sum(0)


def s_cold(H, **_):
    last = np.full(H.shape[1], len(H))
    for i in range(len(H) - 1, -1, -1):
        last = np.where((H[i] == 1) & (last == len(H)), len(H) - 1 - i, last)
    return last


def s_markov(H, **_):
    prev, nxt = H[:-1], H[1:]
    trans = (prev.T @ nxt) / np.maximum(prev.sum(0)[:, None], 1)   # P(n next | m now)
    return H[-1] @ trans


def _windows(H):
    X = np.array([H[i - WINDOW:i].ravel() for i in range(WINDOW, len(H))])
    return X, H[WINDOW:]


class _MLModel:
    def __init__(self, kind):
        self.kind, self.model, self.trained_at = kind, None, -1

    def __call__(self, H, **_):
        if self.model is None or len(H) - self.trained_at >= RETRAIN_EVERY:
            X, Y = _windows(H)
            if self.kind == "mlp":
                from sklearn.neural_network import MLPClassifier
                self.model = MLPClassifier(hidden_layer_sizes=(64,), max_iter=300, random_state=0)
            elif self.kind == "forest":
                from sklearn.ensemble import RandomForestClassifier
                self.model = RandomForestClassifier(n_estimators=150, min_samples_leaf=3, random_state=0, n_jobs=-1)
            else:
                from sklearn.linear_model import LogisticRegression
                from sklearn.multioutput import MultiOutputClassifier
                self.model = MultiOutputClassifier(LogisticRegression(C=0.3, max_iter=300))
            self.model.fit(X, Y)
            self.trained_at = len(H)
        x = H[-WINDOW:].ravel()[None, :]
        p = self.model.predict_proba(x)
        if isinstance(p, list):                       # one array per number
            return np.array([q[0, -1] if q.shape[1] > 1 else 0.0 for q in p])
        return p[0]


METHODS = {
    "random": ("Ngẫu nhiên (mốc so sánh)", lambda: s_random),
    "frequency": ("Tần suất toàn bộ lịch sử", lambda: s_frequency),
    "hot": ("Số nóng (10 kỳ gần nhất)", lambda: s_hot),
    "cold": ("Số lạnh (lâu chưa ra)", lambda: s_cold),
    "markov": ("Chuỗi Markov (kỳ trước → kỳ sau)", lambda: s_markov),
    "logistic": ("Hồi quy logistic, 5 kỳ gần nhất", lambda: _MLModel("logistic")),
    "forest": ("Random Forest, 5 kỳ gần nhất", lambda: _MLModel("forest")),
    "mlp": ("Mạng nơ-ron (như các repo LSTM), 5 kỳ gần nhất", lambda: _MLModel("mlp")),
}


def evaluate(game: Game, draws: list[dict], min_train: int = 60) -> dict:
    H = onehot(game, draws)
    start = max(min_train, len(draws) // 4)
    chance_topk = game.pick * game.drawn / game.pool
    out = {"game": game.id, "draws": len(draws), "tested": len(draws) - start,
           "chance_topk": chance_topk, "methods": {}}
    for key, (label, make) in METHODS.items():
        fn, rng = make(), np.random.default_rng(7)
        topk, ranks = [], []
        for i in range(start, len(draws)):
            scores = fn(H[:i], rng=rng) + rng.random(game.pool) * 1e-9   # random tie-break
            order = np.argsort(-scores)
            top = set(order[:game.pick] + 1)
            topk.append(len(top & set(draws[i]["main"])))
            pct = 1 - np.argsort(order) / (game.pool - 1)               # 1 = ranked first
            ranks.extend(pct[np.array(draws[i]["main"]) - 1])
        topk, ranks = np.array(topk, float), np.array(ranks)
        out["methods"][key] = {
            "label": label,
            "topk_mean": float(topk.mean()),
            "topk_p": float(sps.ttest_1samp(topk, chance_topk).pvalue),
            "rank_mean": float(ranks.mean()),
            "rank_p": float(sps.ttest_1samp(ranks, 0.5).pvalue),
        }
        m = out["methods"][key]
        print(f"  {label:48s} top-{game.pick}: {m['topk_mean']:.3f} (chance {chance_topk:.3f}, p={m['topk_p']:.2f})"
              f"  rank: {m['rank_mean']:.3f} (chance 0.500, p={m['rank_p']:.2f})", flush=True)
    return out


if __name__ == "__main__":
    ids = sys.argv[1:] or list(GAMES)
    for gid in ids:
        g = GAMES[gid]
        draws = load_draws(g)
        print(f"[{g.name}] {len(draws)} draws")
        res = evaluate(g, draws)
        write_json(DATA / g.id / "models.json", res)
