"""Game rules for the three supported games (current matrices only)."""
from __future__ import annotations

from dataclasses import dataclass
from math import comb


@dataclass(frozen=True)
class Game:
    id: str
    name: str
    product_ids: tuple[str, ...]   # product ids used by The Lott API
    pool: int                      # main numbers 1..pool
    pick: int                      # main numbers a player picks per line
    drawn: int                     # main numbers drawn
    supps: int                     # supplementary numbers drawn (0 for Powerball)
    pb_pool: int                   # Powerball pool (0 if none)
    draw_weekday: int              # Monday=0 ... Sunday=6 (Sydney time)
    matrix_start: str              # first draw date of the current matrix
    history_start: str             # first year to backfill
    division_rules: tuple[str, ...]  # human readable, index 0 = division 1

    # -- prize division for a line given match counts ------------------------
    def division(self, m: int, s: int = 0, pb: bool = False) -> int | None:
        if self.id == "oz":           # 7 / 6+1 / 6 / 5+1 / 5 / 4 / 3+1
            if m == 7: return 1
            if m == 6: return 2 if s >= 1 else 3
            if m == 5: return 4 if s >= 1 else 5
            if m == 4: return 6
            if m == 3 and s >= 1: return 7
            return None
        if self.id == "saturday":     # 6 / 5+1 / 5 / 4 / 3+1 / 3
            # Division 6 is "3 numbers" (checked against real winner counts since 2022;
            # the old rule "1-2 numbers + both supps" no longer applies)
            if m == 6: return 1
            if m == 5: return 2 if s >= 1 else 3
            if m == 4: return 4
            if m == 3: return 5 if s >= 1 else 6
            return None
        # powerball
        table = {(7, True): 1, (7, False): 2, (6, True): 3, (6, False): 4, (5, True): 5,
                 (4, True): 6, (5, False): 7, (3, True): 8, (2, True): 9}
        return table.get((m, pb))

    @property
    def n_divisions(self) -> int:
        return len(self.division_rules)

    def division_odds(self) -> list[dict]:
        """Exact probability of each prize division for a single line."""
        probs = [0.0] * (self.n_divisions + 1)
        if self.pb_pool:
            total = comb(self.pool, self.drawn)
            for m in range(self.pick + 1):
                pm = comb(self.drawn, m) * comb(self.pool - self.drawn, self.pick - m) / total
                for pb, ppb in ((True, 1 / self.pb_pool), (False, 1 - 1 / self.pb_pool)):
                    d = self.division(m, 0, pb)
                    if d: probs[d] += pm * ppb
        else:
            others = self.pool - self.drawn - self.supps
            total = comb(self.pool, self.pick)
            for m in range(self.pick + 1):
                for s in range(min(self.supps, self.pick - m) + 1):
                    d = self.division(m, s)
                    if d:
                        probs[d] += comb(self.drawn, m) * comb(self.supps, s) * comb(others, self.pick - m - s) / total
        return [{"division": i + 1, "rule": self.division_rules[i], "p": probs[i + 1],
                 "one_in": round(1 / probs[i + 1], 1)} for i in range(self.n_divisions)]

    def any_prize_p(self) -> float:
        return sum(d["p"] for d in self.division_odds())

    def to_meta(self) -> dict:
        return {"id": self.id, "name": self.name, "pool": self.pool, "pick": self.pick,
                "drawn": self.drawn, "supps": self.supps, "pb_pool": self.pb_pool,
                "draw_weekday": self.draw_weekday, "odds": self.division_odds(),
                "any_prize_p": self.any_prize_p()}


OZ = Game(
    id="oz", name="Oz Lotto", product_ids=("OzLotto",),
    pool=47, pick=7, drawn=7, supps=3, pb_pool=0, draw_weekday=1,
    matrix_start="2022-05-31", history_start="1994",
    division_rules=("7 số", "6 số + số phụ", "6 số", "5 số + số phụ", "5 số", "4 số", "3 số + số phụ"),
)

POWERBALL = Game(
    id="powerball", name="Powerball", product_ids=("Powerball",),
    pool=35, pick=7, drawn=7, supps=0, pb_pool=20, draw_weekday=3,
    matrix_start="2018-04-19", history_start="1996",
    division_rules=("7 số + PB", "7 số", "6 số + PB", "6 số", "5 số + PB", "4 số + PB",
                    "5 số", "3 số + PB", "2 số + PB"),
)

SATURDAY = Game(
    id="saturday", name="Saturday Lotto", product_ids=("TattsLotto", "SaturdayLotto"),
    pool=45, pick=6, drawn=6, supps=2, pb_pool=0, draw_weekday=5,
    matrix_start="1985-01-01", history_start="1985",
    division_rules=("6 số", "5 số + số phụ", "5 số", "4 số", "3 số + số phụ", "3 số"),
)

# Order = order of completion requested by the user.
GAMES: dict[str, Game] = {g.id: g for g in (OZ, POWERBALL, SATURDAY)}


def check_line(game: Game, line: dict, draw: dict) -> dict:
    """Compare one line {"main": [...], "pb": n?} with a draw {"main", "supp"}."""
    s = set(line["main"])
    m = len(s & set(draw["main"]))
    if game.pb_pool:
        pb = bool(draw.get("supp")) and line.get("pb") == draw["supp"][0]
        return {"m": m, "s": 0, "pb": pb, "div": game.division(m, 0, pb)}
    sp = len(s & set(draw.get("supp") or []))
    return {"m": m, "s": sp, "pb": False, "div": game.division(m, sp)}


def valid_draw(game: Game, draw: dict) -> bool:
    """True if a draw matches the game's current matrix (filters out old formats)."""
    main, supp = draw.get("main") or [], draw.get("supp") or []
    if draw.get("date", "") < game.matrix_start:
        return False
    if len(main) != game.drawn or len(set(main)) != game.drawn:
        return False
    if not all(1 <= n <= game.pool for n in main):
        return False
    if game.pb_pool:
        return len(supp) >= 1 and 1 <= supp[0] <= game.pb_pool
    return len(supp) == game.supps and all(1 <= n <= game.pool for n in supp)
