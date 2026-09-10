// Typed client for the backend API (all reads served from SQLite).

export interface TeamTotals {
  abbr: string; name: string; conference: string | null; division: string | null;
  color: string | null; color2: string | null; logo: string | null;
  games: number; pass_yds: number; rush_yds: number; rec_yds: number;
  pass_td: number; rush_td: number; rec_td: number;
}

export interface SeasonPhases {
  season: number;
  phases: { phase: string; games: number; played: number }[];
}

export interface TeamDetail {
  team: { abbr: string; name: string; color: string | null; color2: string | null;
          logo: string | null; conference: string | null; division: string | null };
  season: number; phase: string;
  totals: { games: number; pass_yds: number; rush_yds: number; rec_yds: number };
  games: { game_id: string; week: number; kickoff: string | null; opponent: string;
           home: boolean; team_score: number | null; opp_score: number | null;
           status: string; pass_yds: number; rush_yds: number; rec_yds: number }[];
  players: { player_id: string; name: string; position: string | null; headshot: string | null;
             games: number; pass_yds: number; pass_att: number; pass_td: number;
             rush_yds: number; rush_att: number; rush_td: number;
             rec_yds: number; receptions: number; targets: number; rec_td: number }[];
}

export interface QuarterSplit {
  quarter: number; plays: number;
  pass_att: number; pass_cmp: number; pass_yds: number; pass_td: number;
  rush_att: number; rush_yds: number; rush_td: number;
  targets: number; receptions: number; rec_yds: number; rec_td: number;
  total_yds: number; epa_per_play: number | null; success_rate: number | null;
}

export interface PlayerQuarters {
  player: { id: string; name: string; position: string | null; team: string | null;
            headshot: string | null };
  season: number; phase: string; game_id: string | null;
  quarters: QuarterSplit[];
  best_quarter: number | null;
  games: { game_id: string; week: number; home: string; away: string; kickoff: string | null }[];
}

export interface RouteChart {
  player: { id: string; name: string; position: string | null; team: string | null };
  game: { id: string; week?: number; home?: string; away?: string; phase?: string };
  tracking_note: string;
  routes: { play_id: number; quarter: number | null; location: string | null;
            depth: number | null; depth_band: string | null; complete: boolean;
            yards: number | null; yac: number | null; touchdown: boolean;
            epa: number | null; success: boolean; desc: string | null }[];
  carries: { play_id: number; quarter: number | null; location: string | null;
             gap: string | null; yards: number | null; touchdown: boolean;
             success: boolean; desc: string | null }[];
  zones: { location: string; depth_band: string; targets: number; catches: number;
           yards: number; successes: number; success_rate: number }[];
}

export interface CompareMetricSide {
  a: number | null; b: number | null; a_pct: number | null; b_pct: number | null;
  neutral: boolean;
}
export interface CompareData {
  season: number; phase: string;
  a: { abbr: string; name: string; color: string | null; logo: string | null;
       offense: Record<string, number | null>; defense: Record<string, number | null> };
  b: { abbr: string; name: string; color: string | null; logo: string | null;
       offense: Record<string, number | null>; defense: Record<string, number | null> };
  metrics: { key: string; label: string; offense: CompareMetricSide; defense: CompareMetricSide }[];
}

export interface DefenseProfile {
  team: { abbr: string; name: string; color: string | null; logo: string | null };
  season: number; phase: string; games: number;
  headline: string; notes: string[];
  metrics: { key: string; label: string; value: number | null;
             percentile: number | null; identity_only: boolean }[];
}

export interface RosterPlayer {
  id: string; name: string; position: string | null; headshot: string | null;
  avg_snap_pct: number | null;
}

export interface LineupSplit {
  plays: number; pass_plays: number; run_plays: number; pass_rate: number | null;
  pass_yds_per_play: number | null; rush_yds_per_play: number | null;
  pass_epa: number | null; rush_epa: number | null; epa_per_play: number | null;
  success_rate: number | null; games?: number;
}
export interface LineupImpact {
  team: string; side: string; season: number; phase: string;
  players: { id: string; name: string; position: string | null }[];
  method: 'play_level' | 'game_level';
  on: LineupSplit; off: LineupSplit;
  sufficient: boolean; verdict: string;
}

// --- coaching views -------------------------------------------------------

// Every charted rate comes back as {value, n, coverage} rather than a bare
// number, because roughly half of these columns are third-party charting that
// is not present on every play. `coverage` is the fraction of eligible plays
// that carried the underlying column; the UI must never drop it (see
// <ChartedMetric>). All three fields are null/0 for an empty team-season.
export interface Charted {
  value: number | null;
  n: number;
  coverage: number | null;
  // Set when the column does not exist for this season at all rather than
  // merely being sparse -- the FTN fields carry it for 2021. When present it
  // is the authoritative explanation and is shown verbatim.
  reason?: string | null;
}
export interface ChartedAvg extends Charted {
  by_down?: Record<string, { value: number | null; n: number }>;
}
export interface MixEntry { key: string; n: number; rate: number | null }
export interface Mix {
  mix: MixEntry[];
  n: number;
  coverage: number | null;
}
export interface PersonnelMix extends Mix {
  by_down: Record<string, MixEntry[]>;
  by_distance: Record<string, MixEntry[]>;
  by_field_zone: Record<string, MixEntry[]>;
}
export interface DefPersonnelMix extends Mix {
  packages: MixEntry[];
}

export interface CoachTeam {
  abbr: string; name: string; color: string | null; logo: string | null;
}

// Attribution is model-generated and has NOT been reviewed: `verified` is
// false for every row shipped so far. `fell_back_to_head_coach` means the
// coordinator was unknown and the head coach was substituted, and `note`
// carries known nflverse gaps (in-season firings are not recorded for 2024+).
export interface PlayCaller {
  coach_id: string;
  name: string;
  verified: boolean;
  is_head_coach: boolean;
  fell_back_to_head_coach: boolean;
  note: string | null;
}

// FTN charting starts in 2022: for 2021 the `ftn` object is present but each
// member is null. That is a "not charted" state, never a zero.
export interface FtnBlock {
  play_action: Charted | null;
  motion: Charted | null;
  screen: Charted | null;
  rpo: Charted | null;
}

export interface CoachPlaybook {
  team: CoachTeam;
  season: number;
  phase: string;
  play_caller: PlayCaller | null;
  playbook: {
    plays: number; games: number;
    pass_rate: number | null; run_rate: number | null; epa_per_play: number | null;
    xpass: Charted; pass_oe: Charted;
    shotgun_rate: Charted; no_huddle_rate: Charted;
    personnel: PersonnelMix;
    formation: Mix;
    ftn: FtnBlock | null;
  };
  league_ranks: {
    pass_rate: number | null; pass_oe: number | null; shotgun_rate: number | null;
    no_huddle_rate: number | null; epa_per_play: number | null; of: number;
  };
}

export interface ScriptSplit {
  plays: number;
  pass_rate: number | null;
  shotgun_rate: Charted;
  play_action_rate: Charted;
  epa_per_play: number | null;
  success_rate: number | null;
  personnel_mix: MixEntry[];
}

export interface CoachDrives {
  team: CoachTeam;
  season: number;
  phase: string;
  drives: {
    drives: number;
    avg_start_yardline: number | null;
    epa_per_drive: number | null;
    points_per_drive: number | null;
    three_and_out_rate: number | null;
    punt_rate: number | null;
    by_start_zone: { zone: string; drives: number; share: number | null;
                     epa_per_drive: number | null; points_per_drive: number | null;
                     score_rate: number | null }[];
    results: { result: string; n: number; rate: number | null }[];
  };
  league_average: {
    avg_start_yardline: number | null; epa_per_drive: number | null;
    points_per_drive: number | null; three_and_out_rate: number | null;
  };
  script: {
    script_length: number;
    scripted: ScriptSplit;
    rest_of_game: ScriptSplit;
    after_previous_drive: { previous_result: string;
                            opening_play: ScriptSplit; whole_drive: ScriptSplit }[];
  };
}

export interface Trajectory {
  weeks: { week: number; n: number; of: number; share: number | null }[];
  first_half: number | null;
  second_half: number | null;
  trend: number | null;
}

export interface UsagePlayer {
  player_id: string; name: string; position: string | null; headshot: string | null;
  targets: number; target_share: number | null;
  carries: number; carry_share: number | null;
  target_trajectory: Trajectory | null;
  carry_trajectory: Trajectory | null;
  snap_trajectory: Trajectory | null;
  snap_share: number | null;
  route_mix: Mix | null;
}

export interface CoachUsage {
  team: CoachTeam;
  season: number;
  phase: string;
  team_pass_plays: number;
  team_run_plays: number;
  snap_counts_available: boolean;
  players: UsagePlayer[];
  primary_receivers: { player_id: string; name: string;
                       target_share: number | null; trend: number | null }[];
  lead_back: { player_id: string; name: string;
               carry_share: number | null; trend: number | null } | null;
  backfield_is_committee: boolean;
  snap_names_unresolved: string[];
}

export interface DefPlayerRow {
  player_id: string; name: string; position: string | null; games: number;
  pressures: number | null; sacks: number | null; qb_knockdowns: number | null;
  pressures_per_game: number | null; tackles: number | null;
  missed_tackle_pct: number | null; targets: number | null;
  completion_pct_allowed: number | null; passer_rating_allowed: number | null;
  small_sample: boolean;
}

export interface CoachDefense {
  team: CoachTeam;
  season: number;
  phase: string;
  defense: {
    plays: number; games: number;
    personnel: DefPersonnelMix;
    defenders_in_box: ChartedAvg;
    pressure_rate: Charted;
    blitz_rate: Charted;
    man_rate: Charted;
    zone_rate: Charted;
    coverage_types: Mix;
  };
  league_ranks: {
    pressure_rate: number | null; blitz_rate: number | null;
    man_rate: number | null; defenders_in_box: number | null; of: number;
  };
  starters: Record<string, { player_id: string; name: string;
                             position: string | null; weeks_as_starter: number }[]>;
  players: { source: string; players: DefPlayerRow[];
             top_pass_rushers: DefPlayerRow[]; surest_tacklers: DefPlayerRow[];
             most_missed_tackles: DefPlayerRow[] } | null;
}

export interface LiveGame {
  game_id: string; season: number; phase: string; week: number;
  home: string; away: string; home_score: number | null; away_score: number | null;
  kickoff: string | null;
}

// The SPA ships inside the backend image and is served by the same FastAPI
// process that answers /api, so every call is same-origin and a relative path
// is all we need. Dev keeps that shape via the Vite proxy in vite.config.ts.

// One box means no cold start, but it does go away briefly: a deploy restarts
// the unit, and uvicorn drains the in-flight ingest cycle before exiting, so
// the API can be unreachable for 5-15s. Live migration and cloudflared
// reconnects blip it the same way. Retry across a window wide enough to
// outlast a deploy instead of a single quick nudge.
const TIMEOUT_MS = 8_000;          // warm VM answers <1s; an uncached /api/compare is 1-3s
const RETRY_DELAYS_MS = [800, 2400];

class ApiError extends Error {
  // plain field, not a parameter property: tsconfig sets erasableSyntaxOnly
  status: number;
  constructor(status: number, statusText: string) {
    super(`Request failed (${status} ${statusText})`);
    this.name = 'ApiError';
    this.status = status;
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function attempt<T>(path: string): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const r = await fetch(path, { signal: controller.signal });
    if (!r.ok) throw new ApiError(r.status, r.statusText);
    return await r.json();
  } finally {
    clearTimeout(timer);
  }
}

async function get<T>(path: string): Promise<T> {
  for (let i = 0; ; i++) {
    try {
      return await attempt<T>(path);
    } catch (e) {
      // don't retry a 4xx -- the request itself is wrong and will fail again
      if (e instanceof ApiError && e.status < 500) throw e;
      if (i === RETRY_DELAYS_MS.length) throw e;
      await sleep(RETRY_DELAYS_MS[i]);
    }
  }
}

export const api = {
  seasons: () => get<SeasonPhases[]>('/api/seasons'),
  teams: (season: number, phase: string) =>
    get<TeamTotals[]>(`/api/teams?season=${season}&phase=${phase}`),
  team: (abbr: string, season: number, phase: string) =>
    get<TeamDetail>(`/api/teams/${abbr}?season=${season}&phase=${phase}`),
  playerQuarters: (id: string, season: number, phase: string, game?: string | null) =>
    get<PlayerQuarters>(`/api/players/${encodeURIComponent(id)}/quarters?season=${season}&phase=${phase}${game ? `&game=${encodeURIComponent(game)}` : ''}`),
  playerRoutes: (id: string, game: string) =>
    get<RouteChart>(`/api/players/${encodeURIComponent(id)}/routes?game=${encodeURIComponent(game)}`),
  compare: (a: string, b: string, season: number, phase: string) =>
    get<CompareData>(`/api/compare?teamA=${a}&teamB=${b}&season=${season}&phase=${phase}`),
  defense: (abbr: string, season: number, phase: string) =>
    get<DefenseProfile>(`/api/teams/${abbr}/defense?season=${season}&phase=${phase}`),
  roster: (abbr: string, side: string, season: number, phase: string) =>
    get<RosterPlayer[]>(`/api/teams/${abbr}/roster?side=${side}&season=${season}&phase=${phase}`),
  lineupImpact: (abbr: string, side: string, players: string[], season: number, phase: string) =>
    get<LineupImpact>(`/api/teams/${abbr}/lineup-impact?side=${side}&players=${players.map(encodeURIComponent).join(',')}&season=${season}&phase=${phase}`),
  live: () => get<LiveGame[]>('/api/live'),
  // The coaching endpoints also accept ?coach=<id> instead of ?team= on
  // /playbook, but that returns a different, team-less shape; this page is
  // team-scoped so it only ever passes team.
  coachPlaybook: (abbr: string, season: number, phase: string) =>
    get<CoachPlaybook>(`/api/coach/playbook?team=${abbr}&season=${season}&phase=${phase}`),
  coachDrives: (abbr: string, season: number, phase: string, scriptLength?: number) =>
    get<CoachDrives>(`/api/coach/drives?team=${abbr}&season=${season}&phase=${phase}${scriptLength ? `&script_length=${scriptLength}` : ''}`),
  coachUsage: (abbr: string, season: number, phase: string) =>
    get<CoachUsage>(`/api/coach/usage?team=${abbr}&season=${season}&phase=${phase}`),
  coachDefense: (abbr: string, season: number, phase: string) =>
    get<CoachDefense>(`/api/coach/defense?team=${abbr}&season=${season}&phase=${phase}`),
};
