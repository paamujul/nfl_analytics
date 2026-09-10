"""Shared parsing for the nflverse personnel strings, plus the sparse-column contract.

Two things live here because more than one analytics module needs them and a
second copy of either would be a correctness bug rather than duplication.

1. `offense_personnel` / `defense_personnel` are NOT the "11 / 12 / 21"
   shorthand. nflverse ships a positional roll-call, and its shape changed
   mid-backfill:

       2021-2022   "1 RB, 1 TE, 3 WR"                      (skill positions only)
       2023-2025   "1 C, 1 G, 1 QB, 1 RB, 3 T, 2 TE, 2 WR" (all eleven)

   Both eras parse the same way -- count positions, take (RB + FB) as the first
   digit and TE as the second -- which is why this module counts rather than
   pattern-matches. Counting FB as a back is load-bearing: the 2023+ strings
   list FB separately, and dropping it collapses Baltimore's and San
   Francisco's heavy-back packages into 11 personnel. Measured against the 2024
   regular season that rule reproduces BAL 12=31%/11=28%/21=21%/22=14%
   (n=1039), SF 11=49%/21=36% (n=1014) and KC 11=50%/12=36% (n=1069). The
   2021-22 short form never emits FB at all -- a fullback is simply a second RB
   there -- so the rule is era-independent by luck as much as by design.

   Anything unparseable returns None. These strings come off a charting feed;
   raising on a malformed one would take out a whole season's aggregate over a
   handful of rows.

2. `charted()`. Half the interesting columns are charted by a third party and
   are not present on every play, and the fill rate is not stable across
   seasons (was_pressure is ~53% of 2022 scrimmage plays and 100% of 2023+).
   A rate computed over a partial sample is a rate *among charted plays*, and
   that differs from the team's actual tendency exactly to the degree charting
   is non-random -- which nobody has established that it isn't. So every such
   rate leaves this package as {"value", "n", "coverage"} and never as a bare
   float, so the caller is structurally unable to render it without knowing
   what it was computed over.
"""
from __future__ import annotations

import re
from collections import Counter

# "2 TE" / "1 QB". The counts are always small integers and the position token
# is always alphabetic. Anything else in the string is skipped, not fatal.
_SLOT = re.compile(r"(\d+)\s+([A-Za-z]{1,4})")

# Defensive positions folded into the three units of the DL-LB-DB shorthand.
_DL = {"DE", "DT", "NT", "DL"}
_LB = {"ILB", "OLB", "MLB", "LB"}
_DB = {"CB", "FS", "SS", "DB", "S"}


def parse_personnel(text: str | None) -> dict[str, int] | None:
    """{"RB": 1, "TE": 2, "WR": 2, ...} for a personnel string, else None."""
    if not text:
        return None
    counts: Counter[str] = Counter()
    for n, pos in _SLOT.findall(text):
        counts[pos.upper()] += int(n)
    return dict(counts) if counts else None


def offense_grouping(text: str | None) -> str | None:
    """"11", "21", "12"... -- (RB + FB) then TE. None if unparseable.

    A string, not an int: "02" and "11" are both real groupings and int("02")
    would lose the leading digit.
    """
    counts = parse_personnel(text)
    if counts is None:
        return None
    backs = counts.get("RB", 0) + counts.get("FB", 0)
    tes = counts.get("TE", 0)
    if backs > 9 or tes > 9:  # nonsense row; better dropped than charted
        return None
    return f"{backs}{tes}"


def defense_grouping(text: str | None) -> str | None:
    """"4-2-5" style DL-LB-DB counts. None if unparseable."""
    counts = parse_personnel(text)
    if counts is None:
        return None
    dl = sum(v for k, v in counts.items() if k in _DL)
    lb = sum(v for k, v in counts.items() if k in _LB)
    db = sum(v for k, v in counts.items() if k in _DB)
    if dl + lb + db == 0:
        return None
    return f"{dl}-{lb}-{db}"


def defense_package(text: str | None) -> str | None:
    """base / nickel / dime / quarter, from the DB count.

    The DB count is what names a defensive package in coaching language; the
    DL/LB split behind it varies by scheme without changing the name.
    """
    counts = parse_personnel(text)
    if counts is None:
        return None
    db = sum(v for k, v in counts.items() if k in _DB)
    # Base is four defensive backs (two corners, two safeties); nickel adds the
    # fifth, dime the sixth. Counting from three would name every standard
    # nickel look a dime -- Baltimore played 2-4-5 on 64% of its 2024 snaps and
    # that is a nickel front, not a dime.
    if db <= 4:
        return "base"
    if db == 5:
        return "nickel"
    if db == 6:
        return "dime"
    return "quarter"


def charted(hits: int, charted_n: int, total_n: int, digits: int = 3) -> dict:
    """The return shape for any rate drawn from a partially-charted column.

    hits/charted_n is the rate; charted_n/total_n is how much of the population
    it was observed on. `value` is None rather than 0.0 on an empty sample -- a
    team with no charted plays did not run zero of them.
    """
    return {
        "value": round(hits / charted_n, digits) if charted_n else None,
        "n": charted_n,
        "coverage": round(charted_n / total_n, 3) if total_n else None,
    }


def rate_table(counts: dict[str, int], digits: int = 3) -> list[dict]:
    """Counter -> [{"key", "n", "rate"}] sorted by frequency, for a mix."""
    total = sum(counts.values())
    return [
        {"key": k, "n": n, "rate": round(n / total, digits) if total else None}
        for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
