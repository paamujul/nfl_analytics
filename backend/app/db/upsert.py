"""Dialect-aware bulk upsert, shared by the nflverse backfill and the live ingester.

Deliberately free of heavy imports. This used to live in app.data.nflverse_source,
which imports polars at module scope -- and since app.main imports the ingester,
which imports this helper, every API process was paying ~45 MB of RSS and the
matching cold-start time for polars + pyarrow it never actually used.
"""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.dialects.sqlite import insert as _sqlite_insert

# Postgres rejects a statement carrying more than 65535 bind parameters. Derive
# the batch size from the table's width instead of hardcoding it per caller:
# plays is 31 columns wide, so the old flat chunk of 2000 sat at 62k params.
MAX_BIND_PARAMS = 30_000

_INSERT_BY_DIALECT = {"postgresql": _pg_insert, "sqlite": _sqlite_insert}


def _insert_for(session):
    name = session.get_bind().dialect.name
    try:
        return _INSERT_BY_DIALECT[name]
    except KeyError:
        raise RuntimeError(f"upsert_all has no ON CONFLICT support for dialect {name!r}") from None


def _dedupe_by_pk(rows: list[dict], pk_cols: list[str]) -> list[dict]:
    """Collapse rows sharing a primary key, keeping the last.

    SQLite quietly applies last-write-wins when one INSERT names the same
    conflict target twice; Postgres raises "ON CONFLICT DO UPDATE command cannot
    affect row a second time". Duplicates are reachable in practice -- ESPN
    play_id comes from _int(sequenceNumber), which yields 0 for anything
    unparseable, and SnapCount is keyed on (game_id, player_name, team), which
    collides for two same-named players on one roster.
    """
    seen: dict[tuple, dict] = {}
    for row in rows:
        seen[tuple(row[c] for c in pk_cols)] = row
    return list(seen.values())


def _length_limits(cols) -> dict[str, int]:
    """Declared max length per String column, for the truncation guard below."""
    return {c.name: c.type.length for c in cols
            if isinstance(c.type, String) and c.type.length}


def upsert_all(session, model, rows: list[dict], chunk: int | None = None) -> int:
    """Insert rows, updating on primary-key conflict. Idempotent by design."""
    if not rows:
        return 0

    table = model.__table__
    pk_cols = [c.name for c in table.primary_key.columns]
    cols = list(table.columns)

    # Only columns the caller actually supplied are written on conflict. The
    # padding below fills everything else with a default or None, and copying
    # those into DO UPDATE would clobber values owned by the other data source:
    # sync_schedules drops espn_event_id precisely so it won't overwrite what
    # the live ingester wrote, and padding-then-updating defeated that.
    supplied = {key for row in rows for key in row}

    # Multi-row inserts take their column list from the first row, so pad every
    # row to the full column set (using model defaults where declared). The ESPN
    # normalizers emit genuinely heterogeneous dicts -- a kickoff play carries no
    # passing columns at all, and if it sorts first the whole batch loses them.
    defaults = {c.name: c.default.arg for c in cols
                if c.default is not None and c.default.is_scalar}
    rows = [{c.name: r.get(c.name, defaults.get(c.name)) for c in cols} for r in rows]
    rows = _dedupe_by_pk(rows, pk_cols)

    # SQLite ignores VARCHAR lengths; Postgres raises StringDataRightTruncation
    # and takes the whole batch down with it. Names and CDN URLs come from
    # upstream feeds with no contract on length, so clamp rather than fail a
    # night's ingestion over one unusually long value.
    for name, limit in _length_limits(cols).items():
        for row in rows:
            value = row.get(name)
            if isinstance(value, str) and len(value) > limit:
                row[name] = value[:limit]

    insert = _insert_for(session)
    update_names = [c.name for c in cols
                    if c.name not in pk_cols and c.name in supplied]
    if chunk is None:
        chunk = max(1, MAX_BIND_PARAMS // max(1, len(cols)))

    for i in range(0, len(rows), chunk):
        stmt = insert(table).values(rows[i:i + chunk])
        if update_names:
            stmt = stmt.on_conflict_do_update(
                index_elements=pk_cols,
                set_={name: stmt.excluded[name] for name in update_names},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
        session.execute(stmt)
    return len(rows)
