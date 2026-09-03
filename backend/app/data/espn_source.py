"""ESPN site-API client + normalizers.

ESPN is the source for preseason and for live in-progress games (nflverse
play-by-play only covers completed regular/post-season weeks). Normalizers
translate ESPN payloads into the same internal shapes the nflverse loader
produces, so the analytics layer never cares where a game came from.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

import httpx

from app.config import ESPN_SITE_API, ESPN_TO_NFLVERSE_TEAM, PHASE_FROM_ESPN, PHASES
from app.data.timeutil import to_utc_iso

# Play "type.text" values that describe scrimmage pass/rush plays.
PASS_TYPES = {
    "Pass Reception", "Pass Incompletion", "Passing Touchdown",
    "Pass Interception Return", "Interception Return Touchdown", "Sack",
    "Sack Opp Fumble Recovery",
}
RUSH_TYPES = {"Rush", "Rushing Touchdown", "Fumble Recovery (Own)", "Fumble Recovery (Opponent)"}

RE_PASS = re.compile(
    r"(?P<passer>[A-Z][\w'.-]*\.[\w'-]+(?: [JS]r\.)?(?: I{2,3}V?)?) pass"
    r"(?P<inc> incomplete)? (?P<depth>short|deep) (?P<loc>left|middle|right)"
    r"(?: (?:to|intended for) (?P<receiver>[A-Z][\w'.-]*\.[\w'-]+(?: [JS]r\.)?(?: I{2,3}V?)?))?"
)
RE_RUSH = re.compile(
    r"(?P<rusher>[A-Z][\w'.-]*\.[\w'-]+(?: [JS]r\.)?(?: I{2,3}V?)?) "
    r"(?P<dir>up the middle|left end|left tackle|left guard|right guard|right tackle|right end|scrambles)"
)
RE_SACK = re.compile(r"(?P<passer>[A-Z][\w'.-]*\.[\w'-]+(?: [JS]r\.)?(?: I{2,3}V?)?) sacked")

RUN_DIR = {
    "up the middle": ("middle", "guard"),
    "left end": ("left", "end"),
    "left tackle": ("left", "tackle"),
    "left guard": ("left", "guard"),
    "right guard": ("right", "guard"),
    "right tackle": ("right", "tackle"),
    "right end": ("right", "end"),
    "scrambles": ("middle", "scramble"),
}


def team_abbr(espn_abbr: str) -> str:
    return ESPN_TO_NFLVERSE_TEAM.get(espn_abbr, espn_abbr)


def _norm_name_key(name: str) -> str:
    """'C.J. Stroud' / 'C.Stroud' -> 'c.stroud' for play-text matching."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"\s+(Jr\.|Sr\.|II|III|IV|V)$", "", name.strip())
    parts = name.replace(".", ". ").split()
    if not parts:
        return ""
    last = parts[-1].lower().replace("'", "")
    first_initial = parts[0][0].lower()
    return f"{first_initial}.{last}"


class EspnClient:
    def __init__(self, timeout: float = 20.0):
        # note: ESPN's edge 403s many custom User-Agent strings; httpx's default works
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_json(self, url: str, params: dict | None = None) -> dict:
        r = await self._client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    async def scoreboard(self, season: int, phase: str, week: int) -> dict:
        params = {"seasontype": PHASES[phase], "week": week, "dates": season}
        return await self.get_json(f"{ESPN_SITE_API}/scoreboard", params)

    async def summary(self, event_id: str) -> dict:
        return await self.get_json(f"{ESPN_SITE_API}/summary", {"event": event_id})

    async def teams(self) -> dict:
        return await self.get_json(f"{ESPN_SITE_API}/teams", {"limit": 40})


# ---------------------------------------------------------------------------
# Normalizers (pure functions: ESPN json -> internal dict rows)
# ---------------------------------------------------------------------------

def game_id_for(season: int, phase: str, week: int, away: str, home: str) -> str:
    if phase == "pre":
        return f"{season}_P{week:02d}_{away}_{home}"
    # match nflverse convention so reg/post rows merge with nflverse data:
    # reg weeks 1-18, post weeks 19-22
    w = week if phase == "reg" else 18 + week
    return f"{season}_{w:02d}_{away}_{home}"


def normalize_scoreboard(payload: dict) -> list[dict]:
    """Scoreboard json -> list of game rows."""
    season = payload["season"]["year"]
    phase = PHASE_FROM_ESPN.get(payload["season"]["type"], "reg")
    week = payload.get("week", {}).get("number", 0)
    games = []
    for ev in payload.get("events", []):
        comp = ev["competitions"][0]
        home = away = None
        home_score = away_score = None
        for c in comp["competitors"]:
            ab = team_abbr(c["team"]["abbreviation"])
            score = int(c["score"]) if c.get("score") not in (None, "") else None
            if c["homeAway"] == "home":
                home, home_score = ab, score
            else:
                away, away_score = ab, score
        if not home or not away or len(home) > 4 or len(away) > 4:
            continue  # skip Pro Bowl style exhibition entries
        state = ev["status"]["type"]["state"]  # pre|in|post
        status = {"pre": "scheduled", "in": "in", "post": "final"}.get(state, state)
        games.append({
            "id": game_id_for(season, phase, week, away, home),
            "espn_event_id": str(ev["id"]),
            "season": season, "phase": phase, "week": week,
            "home_team": home, "away_team": away,
            "home_score": home_score, "away_score": away_score,
            "status": status, "kickoff": to_utc_iso(ev.get("date")), "source": "espn",
        })
    return games


def _stat_map(labels: list[str], stats: list[str]) -> dict[str, str]:
    return dict(zip(labels, stats))


def _int(v: str | None) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def normalize_boxscore(summary: dict, game_id: str) -> tuple[list[dict], list[dict]]:
    """Summary json -> (player rows, player_game_stat rows)."""
    players: dict[str, dict] = {}
    stats: dict[str, dict] = {}

    for team_block in summary.get("boxscore", {}).get("players", []):
        team = team_abbr(team_block["team"]["abbreviation"])
        for cat in team_block.get("statistics", []):
            cat_name = cat.get("name")
            if cat_name not in ("passing", "rushing", "receiving"):
                continue
            labels = cat.get("labels", [])
            for ath in cat.get("athletes", []):
                a = ath["athlete"]
                pid = f"espn:{a['id']}"
                players.setdefault(pid, {
                    "id": pid, "espn_id": str(a["id"]),
                    "name": a.get("displayName", ""),
                    "position": (a.get("position") or {}).get("abbreviation"),
                    "team": team,
                    "headshot": (a.get("headshot") or {}).get("href")
                        if isinstance(a.get("headshot"), dict) else a.get("headshot"),
                })
                row = stats.setdefault((game_id, pid), {
                    "game_id": game_id, "player_id": pid, "team": team,
                })
                m = _stat_map(labels, ath.get("stats", []))
                if cat_name == "passing":
                    ca = (m.get("C/ATT") or "0/0").split("/")
                    row["pass_cmp"], row["pass_att"] = _int(ca[0]), _int(ca[-1])
                    row["pass_yds"] = _int(m.get("YDS"))
                    row["pass_td"] = _int(m.get("TD"))
                    row["pass_int"] = _int(m.get("INT"))
                    row["sacks_taken"] = _int((m.get("SACKS") or "0-0").split("-")[0])
                elif cat_name == "rushing":
                    row["rush_att"] = _int(m.get("CAR"))
                    row["rush_yds"] = _int(m.get("YDS"))
                    row["rush_td"] = _int(m.get("TD"))
                    row["rush_long"] = _int(m.get("LONG"))
                elif cat_name == "receiving":
                    row["receptions"] = _int(m.get("REC"))
                    row["rec_yds"] = _int(m.get("YDS"))
                    row["rec_td"] = _int(m.get("TD"))
                    row["rec_long"] = _int(m.get("LONG"))
                    row["targets"] = _int(m.get("TGTS"))
    return list(players.values()), list(stats.values())


def _name_lookup(players: list[dict]) -> dict[tuple[str, str], str]:
    """(team, 'c.stroud') -> player_id, built from box-score athletes."""
    lut: dict[tuple[str, str], str] = {}
    for p in players:
        key = (p["team"], _norm_name_key(p["name"]))
        if key[1]:
            lut[key] = p["id"]
    return lut


def normalize_plays(summary: dict, game_id: str, players: list[dict],
                    home: str, away: str) -> list[dict]:
    """Summary drives -> play rows (quarter, yards, parsed participants)."""
    lut = _name_lookup(players)
    rows: list[dict] = []
    drives = summary.get("drives", {})
    all_drives = list(drives.get("previous", []))
    if drives.get("current") and drives["current"].get("id") not in {d.get("id") for d in all_drives}:
        all_drives.append(drives["current"])

    for drive in all_drives:
        posteam = team_abbr((drive.get("team") or {}).get("abbreviation", "") or "")
        defteam = away if posteam == home else home
        for p in drive.get("plays", []):
            ptype = (p.get("type") or {}).get("text", "")
            text = p.get("text", "") or ""
            if ptype in ("Official Timeout", "Timeout", "End Period", "End of Half",
                         "End of Game", "Two-minute warning"):
                continue
            is_pass = ptype in PASS_TYPES
            is_rush = ptype in RUSH_TYPES and "pass" not in text
            play_type = "pass" if is_pass else "run" if is_rush else ptype.lower().replace(" ", "_")

            start = p.get("start") or {}
            row = {
                "game_id": game_id,
                "play_id": _int(p.get("sequenceNumber")),
                "quarter": (p.get("period") or {}).get("number"),
                "clock": (p.get("clock") or {}).get("displayValue"),
                "down": start.get("down") or None,
                "ydstogo": start.get("distance") or None,
                "yardline_100": start.get("yardsToEndzone") or None,
                "posteam": posteam or None, "defteam": defteam if posteam else None,
                "play_type": play_type,
                "yards_gained": float(p.get("statYardage", 0) or 0),
                "touchdown": bool(p.get("scoringPlay")) and "touchdown" in text.lower(),
                "interception": "intercept" in text.lower(),
                "sack": "sacked" in text.lower(),
                "desc": text,
            }

            if is_pass:
                m = RE_PASS.search(text)
                if m:
                    row["pass_depth"] = m.group("depth")
                    row["pass_location"] = m.group("loc")
                    row["complete_pass"] = not m.group("inc") and not row["interception"]
                    row["passer_id"] = lut.get((posteam, _norm_name_key(m.group("passer"))))
                    if m.group("receiver"):
                        row["receiver_id"] = lut.get((posteam, _norm_name_key(m.group("receiver"))))
                elif (s := RE_SACK.search(text)):
                    row["complete_pass"] = False
                    row["passer_id"] = lut.get((posteam, _norm_name_key(s.group("passer"))))
            elif is_rush:
                m = RE_RUSH.search(text)
                if m:
                    row["rusher_id"] = lut.get((posteam, _norm_name_key(m.group("rusher"))))
                    loc, gap = RUN_DIR.get(m.group("dir"), (None, None))
                    row["run_location"], row["run_gap"] = loc, gap
                else:
                    first = re.match(r"(?:\([^)]*\)\s*)?([A-Z][\w'.-]*\.[\w'-]+)", text)
                    if first:
                        row["rusher_id"] = lut.get((posteam, _norm_name_key(first.group(1))))
            rows.append(row)
    return rows


def normalize_summary_header(summary: dict) -> dict | None:
    """Summary json -> game row (works even when scoreboard wasn't fetched)."""
    header = summary.get("header")
    if not header:
        return None
    season = header["season"]["year"]
    phase = PHASE_FROM_ESPN.get(header["season"]["type"], "reg")
    week = header.get("week", 0)
    comp = header["competitions"][0]
    home = away = None
    home_score = away_score = None
    for c in comp["competitors"]:
        ab = team_abbr(c["team"]["abbreviation"])
        score = int(c["score"]) if c.get("score") not in (None, "") else None
        if c["homeAway"] == "home":
            home, home_score = ab, score
        else:
            away, away_score = ab, score
    state = comp["status"]["type"]["state"]
    return {
        "id": game_id_for(season, phase, week, away, home),
        "espn_event_id": str(header["id"]),
        "season": season, "phase": phase, "week": week,
        "home_team": home, "away_team": away,
        "home_score": home_score, "away_score": away_score,
        "status": {"pre": "scheduled", "in": "in", "post": "final"}.get(state, state),
        "kickoff": to_utc_iso(comp.get("date")), "source": "espn",
    }
