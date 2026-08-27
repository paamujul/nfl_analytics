import { useEffect, useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { api } from './api';
import type { LiveGame } from './api';
import { PHASE_LABEL, useSeason } from './state';

function SeasonPicker() {
  const { season, phase, available, setSeason, setPhase } = useSeason();
  const seasons = available.length ? available.map((r) => r.season) : [2025, 2026];
  const phases = available.find((r) => r.season === season)?.phases
    ?? [{ phase: 'pre', games: 0, played: 0 }, { phase: 'reg', games: 0, played: 0 }];
  return (
    <div className="season-picker">
      <select value={season} onChange={(e) => setSeason(Number(e.target.value))} aria-label="Season">
        {seasons.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
      <select value={phase} onChange={(e) => setPhase(e.target.value)} aria-label="Phase">
        {phases.map((p) => (
          <option key={p.phase} value={p.phase}>
            {PHASE_LABEL[p.phase] ?? p.phase}{p.played ? '' : ' (no games yet)'}
          </option>
        ))}
      </select>
    </div>
  );
}

function LiveBanner() {
  const [games, setGames] = useState<LiveGame[]>([]);
  useEffect(() => {
    let stop = false;
    const tick = () => api.live().then((g) => { if (!stop) setGames(g); }).catch(() => {});
    tick();
    const t = setInterval(tick, 30_000);
    return () => { stop = true; clearInterval(t); };
  }, []);
  if (!games.length) return null;
  return (
    <div className="live-banner">
      <span className="live-dot" aria-hidden />
      <span className="badge live">Live</span>
      {games.map((g) => (
        <span key={g.game_id} className="live-game">
          {g.away} {g.away_score ?? 0} – {g.home_score ?? 0} {g.home}
          <small>{PHASE_LABEL[g.phase]} · week {g.week}</small>
        </span>
      ))}
    </div>
  );
}

export default function App() {
  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to="/" className="brand">NFL<span>·</span>Analytics</NavLink>
        <nav>
          <NavLink to="/" end className={({ isActive }) => (isActive ? 'active' : '')}>Teams</NavLink>
          <NavLink to="/compare" className={({ isActive }) => (isActive ? 'active' : '')}>Compare</NavLink>
          <NavLink to="/defense" className={({ isActive }) => (isActive ? 'active' : '')}>Defense lab</NavLink>
        </nav>
        <div className="spacer" />
        <SeasonPicker />
      </header>
      <LiveBanner />
      <Outlet />
    </div>
  );
}
