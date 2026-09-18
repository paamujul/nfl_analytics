import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import type { TeamTotals } from '../api';
import { PHASE_LABEL, useSeason } from '../state';

// The coaching profile is per team (/coach/:abbr), so the nav entry needs a
// landing page. Same team grid as TeamsPage, minus the yardage rows -- the
// point here is picking a team, not comparing them.
export default function CoachIndexPage() {
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

  return (
    <>
      <div className="page-head">
        <h1>Coaching</h1>
        <span className="sub">
          {season} {PHASE_LABEL[phase]?.toLowerCase()} · pick a team for its playbook, drives, usage and defensive profile
        </span>
      </div>
      {!active.length && <div className="loading">No games played yet for this phase.</div>}
      <div className="grid teams-grid">
        {active.map((t) => (
          <Link key={t.abbr} to={`/coach/${t.abbr}`} className="card team-card"
            style={{ ['--team-color' as string]: t.color ?? 'var(--series-1)' }}>
            <div className="head">
              {t.logo && <img src={t.logo} alt="" loading="lazy" />}
              <div>
                <h3>{t.name}</h3>
                <div className="sub">{t.division ?? ''} · {t.games} game{t.games === 1 ? '' : 's'}</div>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </>
  );
}
