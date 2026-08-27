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

export interface LiveGame {
  game_id: string; season: number; phase: string; week: number;
  home: string; away: string; home_score: number | null; away_score: number | null;
  kickoff: string | null;
}

// In production the API lives on a separate host (Railway); set VITE_API_BASE
// to that origin. Empty default keeps dev on the Vite proxy (same origin).
const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '');

async function get<T>(path: string): Promise<T> {
  const url = `${API_BASE}${path}`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${url}`);
  return r.json();
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
};
