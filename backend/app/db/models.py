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
