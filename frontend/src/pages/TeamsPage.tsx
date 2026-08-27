import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import type { TeamTotals } from '../api';
import { PHASE_LABEL, useSeason } from '../state';

// stat identity colors, fixed order (dataviz categorical slots 1/2/3)
export const STAT_COLORS = { pass: 'var(--series-1)', rush: 'var(--series-2)', rec: 'var(--series-3)' };

function YardRow({ label, value, max, color }:
  { label: string; value: number; max: number; color: string }) {
  return (
    <div className="yard-row">
      <span className="lbl">{label}</span>
      <div className="bar-track">
        <div className="bar-fill" style={{ width: `${max ? (value / max) * 100 : 0}%`, background: color }} />
      </div>
      <span className="val">{value.toLocaleString()}</span>
    </div>
  );
}

export default function TeamsPage() {
  const { season, phase } = useSeason();
  const [teams, setTeams] = useState<TeamTotals[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTeams(null); setError(null);
    api.teams(season, phase).then(setTeams).catch((e) => setError(String(e)));
  }, [season, phase]);

  if (error) return <div className="error-box">Couldn’t load teams: {error}</div>;
  if (!teams) return <div className="loading">Loading teams…</div>;

  const active = teams.filter((t) => t.games > 0);
  const max = Math.max(1, ...active.flatMap((t) => [t.pass_yds, t.rush_yds, t.rec_yds]));

  return (
    <>
      <div className="page-head">
        <h1>Teams</h1>
        <span className="sub">{season} {PHASE_LABEL[phase]?.toLowerCase()} · total passing, rushing and receiving yards</span>
      </div>
      {!active.length && <div className="loading">No games played yet for this phase.</div>}
      <div className="grid teams-grid">
        {active.map((t) => (
          <Link key={t.abbr} to={`/team/${t.abbr}`} className="card team-card"
            style={{ ['--team-color' as string]: t.color ?? 'var(--series-1)' }}>
            <div className="head">
              {t.logo && <img src={t.logo} alt="" loading="lazy" />}
              <div>
                <h3>{t.name}</h3>
                <div className="sub">{t.division ?? ''} · {t.games} game{t.games === 1 ? '' : 's'}</div>
              </div>
            </div>
            <div className="yard-rows">
              <YardRow label="Passing" value={t.pass_yds} max={max} color={STAT_COLORS.pass} />
              <YardRow label="Rushing" value={t.rush_yds} max={max} color={STAT_COLORS.rush} />
              <YardRow label="Receiving" value={t.rec_yds} max={max} color={STAT_COLORS.rec} />
            </div>
          </Link>
        ))}
      </div>
    </>
  );
}
