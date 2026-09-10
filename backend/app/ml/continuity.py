"""Per-row training weights: how much should a 2022 snap count when the thing
we care about is 2025?

    weight(play) = decay ** (target_season - play_season)
                 * (W_COACH * coach_term + W_ROSTER * roster_term)

Two independent reasons a three-year-old snap might still be informative, and
they come apart in exactly the case this exists for -- **coach changed but the
starters were retained**, and its mirror, coach stayed but the roster turned
over. A single decay cannot express either.

coach_term
    1.0 if the play's offensive play-caller is still calling plays ANYWHERE in
    the target season, else COACH_FLOOR (0.25).

    "Anywhere", not "for this team", is the point. When a team hires a new
    co-ordinator, the departed caller's snaps drop to the floor, while the new
    caller's snaps AT HIS PREVIOUS TEAM stay at full strength -- which is the
    spec's "weight that caller's own history at their previous team at full
    strength". Because `play_caller_id` is also a model feature, the model can
    condition on whose tendencies those were rather than averaging them in.

roster_term
    Snap-weighted share of the team's target-season offensive starters who were
    already on that team in the play's season. Independent of the coach, so
    "new co-ordinator, same ten returning starters" lands high on this axis and
    at the floor on the other -- the model keeps the personnel context and
    discounts the tendencies.

TARGET IS A SEASON, NOT A TEAM-SEASON. Every row's continuity is measured
against its own team's target-season state, so one global model covers the
league; there is no 32-model-per-season fan-out.

DEPTH CHART VS SNAP COUNTS
--------------------------
The spec called for `depth_chart` (depth_team = 1) plus `snap_counts`. That does
not survive the data: depth_chart holds ~37,000 rows/season for 2021-24 but only
2,291 for 2025 (860 starters, one week's worth, and a different `formation`
vocabulary -- "3WR 1TE" rather than "Offense"). Since 2025 is precisely the
target season, the retention denominator would be built from a near-empty table.

snap_counts is complete and uniform (~26,500 rows every season, 2021-25) and
answers the question more directly anyway: who actually took the offensive snaps
is a better definition of "starter" than who was listed. So snap_counts is the
source, and depth_chart is not used. Note that snap_counts carries player_name
and no player_id, so retention is matched on (name, team) -- self-consistent
because both sides come from the same feed, but it will silently miss a player
whose listed name changed between seasons.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

DECAY_DEFAULT = 0.6      # tuned in train.py; see tune_decay()
COACH_FLOOR = 0.25
W_COACH = 0.6
W_ROSTER = 0.4
N_STARTERS = 11          # offensive starters, by season offensive snaps


@dataclass
class ContinuityModel:
    """Precomputed lookups for weighting rows toward one target season."""
    target_season: int
    decay: float = DECAY_DEFAULT
    coach_floor: float = COACH_FLOOR
    w_coach: float = W_COACH
    w_roster: float = W_ROSTER
    # (season, team) -> caller id
    callers: dict[tuple[int, str], str] = field(default_factory=dict)
    # caller ids still calling plays somewhere in target_season
    active_callers: set[str] = field(default_factory=set)
    # (season, team) -> snap-weighted retention of target-season starters
    retention: dict[tuple[int, str], float] = field(default_factory=dict)

    def coach_term(self, season: int, team: str) -> float:
        caller = self.callers.get((season, team))
        if caller is None:
            return self.coach_floor
        return 1.0 if caller in self.active_callers else self.coach_floor

    def roster_term(self, season: int, team: str) -> float:
        return self.retention.get((season, team), 0.0)

    def continuity(self, season: int, team: str) -> float:
        return (self.w_coach * self.coach_term(season, team)
                + self.w_roster * self.roster_term(season, team))

    def weight(self, season: int, team: str) -> float:
        gap = self.target_season - season
        if gap < 0:
            # A row from the future relative to the target is not decayed --
            # it is excluded upstream by the time split, and silently giving it
            # decay**negative (a number > 1) would quietly dominate the fit.
            raise ValueError(f"season {season} is after target {self.target_season}")
        return (self.decay ** gap) * self.continuity(season, team)


def load_snap_shares(engine, seasons: tuple[int, ...]) -> pl.DataFrame:
    """(season, team, player_name, offense_snaps) summed over the season."""
    lo, hi = min(seasons), max(seasons)
    sql = f"""
        SELECT season, team, player_name, SUM(offense_snaps) AS offense_snaps
        FROM snap_counts
        WHERE season BETWEEN {int(lo)} AND {int(hi)}
        GROUP BY season, team, player_name
    """
    with engine.connect() as conn:
        return pl.read_database(
            sql, connection=conn,
            schema_overrides={"season": pl.Int32, "offense_snaps": pl.Int64},
            infer_schema_length=None)


def build(staff: pl.DataFrame, snaps: pl.DataFrame, target_season: int,
          decay: float = DECAY_DEFAULT, **kw) -> ContinuityModel:
    """Assemble a ContinuityModel from the coaching_staff and snap_counts frames.

    `staff` needs columns (season, team, offensive_play_caller_id); `snaps`
    needs (season, team, player_name, offense_snaps).
    """
    model = ContinuityModel(target_season=target_season, decay=decay, **kw)

    for row in staff.iter_rows(named=True):
        caller = row["offensive_play_caller_id"]
        if caller is None:
            continue
        model.callers[(int(row["season"]), row["team"])] = caller
        if int(row["season"]) == target_season:
            model.active_callers.add(caller)

    model.retention = _retention(snaps, target_season)
    return model


def _retention(snaps: pl.DataFrame, target_season: int) -> dict[tuple[int, str], float]:
    """(season, team) -> snap-weighted share of that team's target-season
    offensive starters who were already on the roster in `season`.

    Starters = the top N_STARTERS players by season offensive snaps. Weighting
    by snap share rather than counting heads means losing a 1,100-snap left
    tackle costs more than losing an 11th man who played 300.
    """
    target = (snaps.filter((pl.col("season") == target_season)
                           & (pl.col("offense_snaps") > 0))
                   .sort(["team", "offense_snaps"], descending=[False, True])
                   .group_by("team", maintain_order=True)
                   .head(N_STARTERS))
    if target.is_empty():
        return {}

    # Who was on each (season, team) at all, for the membership test.
    present: dict[tuple[int, str], set[str]] = {}
    for row in snaps.filter(pl.col("offense_snaps") > 0).iter_rows(named=True):
        present.setdefault((int(row["season"]), row["team"]), set()).add(row["player_name"])

    starters: dict[str, list[tuple[str, float]]] = {}
    for row in target.iter_rows(named=True):
        starters.setdefault(row["team"], []).append(
            (row["player_name"], float(row["offense_snaps"])))

    out: dict[tuple[int, str], float] = {}
    for season in sorted({int(s) for s in snaps["season"].unique()}):
        for team, roster in starters.items():
            total = sum(w for _, w in roster)
            if not total:
                continue
            here = present.get((season, team), set())
            kept = sum(w for name, w in roster if name in here)
            out[(season, team)] = kept / total
    return out


def weight_column(df: pl.DataFrame, model: ContinuityModel) -> pl.Series:
    """Row weights for a feature frame carrying `season` and `posteam`."""
    keys = list(zip(df["season"].to_list(), df["posteam"].to_list()))
    return pl.Series("weight", [model.weight(int(s), t) for s, t in keys],
                     dtype=pl.Float64)
