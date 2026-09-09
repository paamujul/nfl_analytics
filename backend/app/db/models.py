"""SQLAlchemy models — the SQLite system of record.

Every frontend request is served from these tables; external APIs are only
touched by the ingestion service (app/data/ingest.py) and the nflverse
backfill (app/data/nflverse_source.py).
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    abbr: Mapped[str] = mapped_column(String(4), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    conference: Mapped[str | None] = mapped_column(String(4))
    division: Mapped[str | None] = mapped_column(String(16))
    color: Mapped[str | None] = mapped_column(String(9))
    color2: Mapped[str | None] = mapped_column(String(9))
    logo: Mapped[str | None] = mapped_column(String(512))
    espn_id: Mapped[str | None] = mapped_column(String(8))


class Player(Base):
    __tablename__ = "players"

    # gsis id ("00-0033873") for nflverse players; "espn:{athleteId}" for
    # players only seen through ESPN (preseason bodies mostly).
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    espn_id: Mapped[str | None] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    position: Mapped[str | None] = mapped_column(String(8))
    team: Mapped[str | None] = mapped_column(String(4), index=True)
    headshot: Mapped[str | None] = mapped_column(String(512))


class Game(Base):
    __tablename__ = "games"

    # nflverse style id, e.g. "2025_01_KC_LAC"; ESPN preseason games get
    # "2026_P03_LV_HOU".
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    espn_event_id: Mapped[str | None] = mapped_column(String(16), unique=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    phase: Mapped[str] = mapped_column(String(4), index=True)  # pre|reg|post
    week: Mapped[int] = mapped_column(Integer)
    home_team: Mapped[str] = mapped_column(String(4), index=True)
    away_team: Mapped[str] = mapped_column(String(4), index=True)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="scheduled")  # scheduled|in|final
    kickoff: Mapped[str | None] = mapped_column(String(32))  # ISO datetime
    source: Mapped[str] = mapped_column(String(12), default="espn")  # espn|nflverse


class PlayerGameStat(Base):
    __tablename__ = "player_game_stats"

    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), primary_key=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), primary_key=True)
    team: Mapped[str] = mapped_column(String(4), index=True)

    pass_att: Mapped[int] = mapped_column(Integer, default=0)
    pass_cmp: Mapped[int] = mapped_column(Integer, default=0)
    pass_yds: Mapped[int] = mapped_column(Integer, default=0)
    pass_td: Mapped[int] = mapped_column(Integer, default=0)
    pass_int: Mapped[int] = mapped_column(Integer, default=0)
    sacks_taken: Mapped[int] = mapped_column(Integer, default=0)

    rush_att: Mapped[int] = mapped_column(Integer, default=0)
    rush_yds: Mapped[int] = mapped_column(Integer, default=0)
    rush_td: Mapped[int] = mapped_column(Integer, default=0)
    rush_long: Mapped[int] = mapped_column(Integer, default=0)

    targets: Mapped[int] = mapped_column(Integer, default=0)
    receptions: Mapped[int] = mapped_column(Integer, default=0)
    rec_yds: Mapped[int] = mapped_column(Integer, default=0)
    rec_td: Mapped[int] = mapped_column(Integer, default=0)
    rec_long: Mapped[int] = mapped_column(Integer, default=0)

    # Defensive box score. These ship in the same load_player_stats download the
    # offensive columns above come from -- we were parsing the file and dropping
    # them. Nullable rather than default-0 because the ESPN live ingester writes
    # this table too and has no defensive equivalent: a 0 there would be a lie,
    # whereas NULL correctly says "this source didn't tell us".
    def_tackles_solo: Mapped[int | None] = mapped_column(Integer)
    def_tackle_assists: Mapped[int | None] = mapped_column(Integer)
    def_tackles_for_loss: Mapped[float | None] = mapped_column(Float)
    def_fumbles_forced: Mapped[int | None] = mapped_column(Integer)
    def_sacks: Mapped[float | None] = mapped_column(Float)  # half-sacks: 2.5 is real
    def_qb_hits: Mapped[int | None] = mapped_column(Integer)
    def_interceptions: Mapped[int | None] = mapped_column(Integer)
    def_pass_defended: Mapped[int | None] = mapped_column(Integer)
    def_tds: Mapped[int | None] = mapped_column(Integer)


class Play(Base):
    __tablename__ = "plays"

    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), primary_key=True)
    play_id: Mapped[int] = mapped_column(Integer, primary_key=True)

    quarter: Mapped[int | None] = mapped_column(Integer)
    clock: Mapped[str | None] = mapped_column(String(8))
    down: Mapped[int | None] = mapped_column(Integer)
    ydstogo: Mapped[int | None] = mapped_column(Integer)
    yardline_100: Mapped[int | None] = mapped_column(Integer)
    posteam: Mapped[str | None] = mapped_column(String(4), index=True)
    defteam: Mapped[str | None] = mapped_column(String(4), index=True)
    play_type: Mapped[str | None] = mapped_column(String(16))  # pass|run|punt|...
    yards_gained: Mapped[float | None] = mapped_column(Float)

    air_yards: Mapped[float | None] = mapped_column(Float)
    yac: Mapped[float | None] = mapped_column(Float)
    pass_location: Mapped[str | None] = mapped_column(String(8))  # left|middle|right
    pass_depth: Mapped[str | None] = mapped_column(String(8))  # short|deep
    run_gap: Mapped[str | None] = mapped_column(String(8))
    run_location: Mapped[str | None] = mapped_column(String(8))

    passer_id: Mapped[str | None] = mapped_column(String(24), index=True)
    rusher_id: Mapped[str | None] = mapped_column(String(24), index=True)
    receiver_id: Mapped[str | None] = mapped_column(String(24), index=True)

    complete_pass: Mapped[bool | None] = mapped_column(Boolean)
    touchdown: Mapped[bool] = mapped_column(Boolean, default=False)
    interception: Mapped[bool] = mapped_column(Boolean, default=False)
    sack: Mapped[bool] = mapped_column(Boolean, default=False)

    epa: Mapped[float | None] = mapped_column(Float)
    success: Mapped[bool | None] = mapped_column(Boolean)

    # ";"-joined gsis ids of players on the field (nflverse participation).
    offense_players: Mapped[str | None] = mapped_column(Text)
    defense_players: Mapped[str | None] = mapped_column(Text)
    n_pass_rushers: Mapped[int | None] = mapped_column(Integer)
    is_blitz: Mapped[bool | None] = mapped_column(Boolean)

    # --- play-call context (load_pbp) ------------------------------------
    # Everything below is nullable with no default. Fill rates vary by season
    # and by source file (see sync_pbp's docstring) -- consumers MUST treat
    # None as "not charted", never as a zero or a False.
    shotgun: Mapped[bool | None] = mapped_column(Boolean)
    no_huddle: Mapped[bool | None] = mapped_column(Boolean)
    qb_dropback: Mapped[bool | None] = mapped_column(Boolean)
    qb_scramble: Mapped[bool | None] = mapped_column(Boolean)
    goal_to_go: Mapped[bool | None] = mapped_column(Boolean)
    first_down: Mapped[bool | None] = mapped_column(Boolean)
    penalty: Mapped[bool | None] = mapped_column(Boolean)
    pass_length: Mapped[str | None] = mapped_column(String(8))  # short|deep (charted)

    # Drive / series context. Stored as ints; nflverse ships them as Float64
    # purely because the column is nullable in parquet.
    drive: Mapped[int | None] = mapped_column(Integer)
    fixed_drive: Mapped[int | None] = mapped_column(Integer)
    fixed_drive_result: Mapped[str | None] = mapped_column(String(24))
    drive_start_yard_line: Mapped[str | None] = mapped_column(String(12))  # "WAS 25"
    drive_play_count: Mapped[int | None] = mapped_column(Integer)
    series: Mapped[int | None] = mapped_column(Integer)
    series_result: Mapped[str | None] = mapped_column(String(24))

    # Game state at snap -- the situational features a play-call model needs.
    score_differential: Mapped[float | None] = mapped_column(Float)
    game_seconds_remaining: Mapped[float | None] = mapped_column(Float)
    half_seconds_remaining: Mapped[float | None] = mapped_column(Float)
    posteam_timeouts_remaining: Mapped[int | None] = mapped_column(Integer)
    wp: Mapped[float | None] = mapped_column(Float)  # win probability for posteam

    # nflverse's own pass-probability model and pass-rate-over-expected. This is
    # the baseline any later play-call model has to beat, so it is stored rather
    # than recomputed.
    xpass: Mapped[float | None] = mapped_column(Float)
    pass_oe: Mapped[float | None] = mapped_column(Float)

    # --- personnel & coverage (load_participation) -----------------------
    offense_formation: Mapped[str | None] = mapped_column(String(16))  # SHOTGUN|PISTOL|...
    # 2021-22 uses the short skill-position form ("1 RB, 1 TE, 3 WR"); 2023+
    # lists all eleven ("1 C, 1 G, 1 QB, 1 RB, 3 T, 2 TE, 2 WR"), which measures
    # up to 66 chars. Sized with headroom -- upsert_all clamps overlong strings
    # silently, so an under-sized column here would quietly corrupt personnel.
    offense_personnel: Mapped[str | None] = mapped_column(String(80))
    defense_personnel: Mapped[str | None] = mapped_column(String(80))
    defenders_in_box: Mapped[int | None] = mapped_column(Integer)
    was_pressure: Mapped[bool | None] = mapped_column(Boolean)
    defense_man_zone_type: Mapped[str | None] = mapped_column(String(16))  # MAN_/ZONE_COVERAGE
    defense_coverage_type: Mapped[str | None] = mapped_column(String(16))  # COVER_3|COMBO|...
    route: Mapped[str | None] = mapped_column(String(24))  # targeted receiver's route
    time_to_throw: Mapped[float | None] = mapped_column(Float)
    n_offense: Mapped[int | None] = mapped_column(Integer)
    n_defense: Mapped[int | None] = mapped_column(Integer)

    # --- FTN charting (load_ftn_charting, 2022+) -------------------------
    # These are typed fields, which is why nothing in this codebase parses the
    # `desc` prose for play-action / motion / screen / RPO.
    is_play_action: Mapped[bool | None] = mapped_column(Boolean)
    is_motion: Mapped[bool | None] = mapped_column(Boolean)
    is_screen_pass: Mapped[bool | None] = mapped_column(Boolean)
    is_rpo: Mapped[bool | None] = mapped_column(Boolean)
    is_trick_play: Mapped[bool | None] = mapped_column(Boolean)
    is_qb_sneak: Mapped[bool | None] = mapped_column(Boolean)
    qb_location: Mapped[str | None] = mapped_column(String(2))  # U(nder)|S(hotgun)|P(istol)
    n_offense_backfield: Mapped[int | None] = mapped_column(Integer)
    n_defense_box: Mapped[int | None] = mapped_column(Integer)

    desc: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_plays_game_quarter", "game_id", "quarter"),
    )


class SnapCount(Base):
    __tablename__ = "snap_counts"

    game_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    player_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    team: Mapped[str] = mapped_column(String(4), primary_key=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    week: Mapped[int] = mapped_column(Integer)
    position: Mapped[str | None] = mapped_column(String(8))
    opponent: Mapped[str | None] = mapped_column(String(4))
    offense_snaps: Mapped[int] = mapped_column(Integer, default=0)
    offense_pct: Mapped[float] = mapped_column(Float, default=0.0)
    defense_snaps: Mapped[int] = mapped_column(Integer, default=0)
    defense_pct: Mapped[float] = mapped_column(Float, default=0.0)


class Coach(Base):
    """A coach, keyed by a kebab-case slug derived from the name ("andy-reid").

    No nflverse dataset carries coach identity, so there is no upstream id to
    borrow. The slug is stable enough for the one job it has -- joining
    coaching_staff rows to a display name -- and readable in a query result.
    """
    __tablename__ = "coaches"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))


class CoachingStaff(Base):
    """Who held each staff role for one team in one season.

    The play-caller columns are the reason this table exists. On a large
    minority of staffs the head coach calls the offensive plays rather than the
    OC (and defensive play-calling splits the same way), so crediting a call to
    the coordinator by default silently misattributes it. Where the caller is
    unknown the column is NULL and consumers should fall back to the head coach
    -- sync_coaching_staff logs each fallback it applies.

    Head coaches are derived from load_schedules and are ground truth.
    Coordinators are hand-entered, unverified, and frequently NULL on purpose;
    `verified` says whether a human has checked the row. Treat verified=False
    coordinator/play-caller fields as a hypothesis, not a fact.
    """
    __tablename__ = "coaching_staff"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    team: Mapped[str] = mapped_column(String(4), primary_key=True)

    head_coach_id: Mapped[str | None] = mapped_column(ForeignKey("coaches.id"))
    oc_id: Mapped[str | None] = mapped_column(ForeignKey("coaches.id"))
    dc_id: Mapped[str | None] = mapped_column(ForeignKey("coaches.id"))
    offensive_play_caller_id: Mapped[str | None] = mapped_column(ForeignKey("coaches.id"))
    defensive_play_caller_id: Mapped[str | None] = mapped_column(ForeignKey("coaches.id"))

    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)  # e.g. mid-season HC change


class DepthChartEntry(Base):
    """Weekly depth chart -- who is listed where, and how deep.

    Answers "who are this team's edge rushers / corners / safeties" and later
    feeds roster-continuity weighting.

    Two upstream shapes are folded into this one table (see sync_depth_charts):
    2021-2024 publish a per-week, per-formation chart; 2025 publishes dated
    roster snapshots with no week at all, which land here as week=0.
    """
    __tablename__ = "depth_chart"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    week: Mapped[int] = mapped_column(Integer, primary_key=True)  # 0 = undated snapshot
    game_type: Mapped[str] = mapped_column(String(8), primary_key=True, default="REG")
    team: Mapped[str] = mapped_column(String(4), primary_key=True)
    formation: Mapped[str] = mapped_column(String(16), primary_key=True)  # Offense|Defense|Special Teams
    depth_position: Mapped[str] = mapped_column(String(8), primary_key=True)  # RG|LT|CB|...
    player_id: Mapped[str] = mapped_column(String(24), primary_key=True)  # gsis id

    depth_team: Mapped[int | None] = mapped_column(Integer)  # 1 = starter
    position: Mapped[str | None] = mapped_column(String(8))
    player_name: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        Index("ix_depth_chart_season_team", "season", "team"),
        Index("ix_depth_chart_player", "player_id"),
    )


class PlayerSeasonAdvanced(Base):
    """PFR advanced defensive stats, one row per player-season.

    Upstream keys on `pfr_id`, not a gsis id, so it will not join to `players`
    as shipped. sync_pfr_advstats resolves pfr_id -> gsis_id through
    load_players() (which carries both) and stores the gsis id in `player_id`;
    that mapping resolved 926/926 rows for 2023, so the join is safe to rely on.
    `pfr_id` is kept alongside for traceability back to the source row, and
    `player_id` is nullable for the rare unmapped player.
    """
    __tablename__ = "player_season_advanced"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    pfr_id: Mapped[str] = mapped_column(String(16), primary_key=True)

    player_id: Mapped[str | None] = mapped_column(String(24), index=True)  # gsis id
    player_name: Mapped[str | None] = mapped_column(String(128))
    team: Mapped[str | None] = mapped_column(String(4))  # "2TM" for mid-season trades
    position: Mapped[str | None] = mapped_column(String(8))
    games: Mapped[int | None] = mapped_column(Integer)
    games_started: Mapped[int | None] = mapped_column(Integer)

    # pass rush
    prss: Mapped[float | None] = mapped_column(Float)  # pressures
    hrry: Mapped[float | None] = mapped_column(Float)  # hurries
    qbkd: Mapped[float | None] = mapped_column(Float)  # QB knockdowns
    bltz: Mapped[float | None] = mapped_column(Float)  # times blitzed
    sk: Mapped[float | None] = mapped_column(Float)    # sacks (halves)

    # coverage
    tgt: Mapped[float | None] = mapped_column(Float)
    cmp_percent: Mapped[float | None] = mapped_column(Float)
    rat: Mapped[float | None] = mapped_column(Float)   # passer rating when targeted
    dadot: Mapped[float | None] = mapped_column(Float)  # avg depth of target

    # tackling
    comb: Mapped[float | None] = mapped_column(Float)
    m_tkl_percent: Mapped[float | None] = mapped_column(Float)


class SyncLog(Base):
    __tablename__ = "sync_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(12))  # espn|nflverse
    scope: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(8))  # ok|error
    message: Mapped[str | None] = mapped_column(Text)
    rows: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(
        String(32),
        default=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    __table_args__ = (Index("ix_sync_log_status_id", "status", "id"),)
