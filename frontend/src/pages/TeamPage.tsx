import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { api } from '../api';
import type { TeamDetail } from '../api';
import { PHASE_LABEL, useSeason } from '../state';
import { STAT_COLORS } from './TeamsPage';
import { VizTooltip } from '../components/VizTooltip';

export default function TeamPage() {
  const { abbr = '' } = useParams();
  const { season, phase } = useSeason();
  const [data, setData] = useState<TeamDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null); setError(null);
    api.team(abbr, season, phase).then(setData).catch((e) => setError(String(e)));
  }, [abbr, season, phase]);

  if (error) return <div className="error-box">Couldn’t load team: {error}</div>;
  if (!data) return <div className="loading">Loading {abbr}…</div>;

  const { team, totals } = data;
  const played = data.games.filter((g) => g.status !== 'scheduled');
  const chart = played.map((g) => ({
    name: `W${g.week} ${g.home ? 'vs' : '@'} ${g.opponent}`,
    Passing: g.pass_yds, Rushing: g.rush_yds,
  }));

  return (
    <>
      <div className="page-head">
        {team.logo && <img src={team.logo} alt="" />}
        <span className="accent-chip" style={{ background: team.color ?? 'var(--series-1)' }} />
        <div>
          <h1>{team.name}</h1>
          <span className="sub">
            {team.division} · {season} {PHASE_LABEL[phase]?.toLowerCase()} ·{' '}
            <Link to="/defense" style={{ color: 'var(--series-1)', fontWeight: 600 }}>defense profile →</Link>
          </span>
        </div>
      </div>

      <div className="stat-row">
        <div className="card stat-tile">
          <div className="k">Games</div><div className="v">{totals.games}</div>
        </div>
        <div className="card stat-tile">
          <div className="k">Passing yards</div>
          <div className="v" style={{ color: STAT_COLORS.pass }}>{totals.pass_yds.toLocaleString()}</div>
          <div className="u">{totals.games ? Math.round(totals.pass_yds / totals.games) : 0} per game</div>
        </div>
        <div className="card stat-tile">
          <div className="k">Rushing yards</div>
          <div className="v" style={{ color: STAT_COLORS.rush }}>{totals.rush_yds.toLocaleString()}</div>
          <div className="u">{totals.games ? Math.round(totals.rush_yds / totals.games) : 0} per game</div>
        </div>
        <div className="card stat-tile">
          <div className="k">Receiving yards</div>
          <div className="v" style={{ color: STAT_COLORS.rec }}>{totals.rec_yds.toLocaleString()}</div>
          <div className="u">equals team passing</div>
        </div>
      </div>

      {chart.length > 0 && (
        <div className="card">
          <h2 className="section" style={{ marginTop: 0 }}>Game by game<small>passing vs rushing yards</small></h2>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={chart} margin={{ top: 4, right: 8, left: -18, bottom: 0 }} barGap={2}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="name" tick={{ fill: 'var(--muted)', fontSize: 11 }}
                tickLine={false} axisLine={{ stroke: 'var(--baseline)' }} interval={0}
                angle={chart.length > 8 ? -35 : 0} height={chart.length > 8 ? 58 : 24}
                textAnchor={chart.length > 8 ? 'end' : 'middle'} />
              <YAxis tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip content={<VizTooltip />} cursor={{ fill: 'color-mix(in oklab, var(--series-1) 7%, transparent)' }} />
              <Legend wrapperStyle={{ fontSize: 12.5 }} />
              <Bar dataKey="Passing" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={26} />
              <Bar dataKey="Rushing" fill="var(--series-2)" radius={[4, 4, 0, 0]} maxBarSize={26} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      <h2 className="section">Players<small>click a player for quarter-by-quarter analytics and route charts</small></h2>
      <div className="card" style={{ overflowX: 'auto', padding: '6px 12px' }}>
        <table className="data">
          <thead>
            <tr>
              <th>Player</th><th>G</th>
              <th>Pass yds</th><th>Pass TD</th>
              <th>Rush yds</th><th>Rush TD</th>
              <th>Rec</th><th>Tgt</th><th>Rec yds</th><th>Rec TD</th>
            </tr>
          </thead>
          <tbody>
            {data.players.map((p) => (
              <tr key={p.player_id}>
                <td>
                  <Link to={`/player/${encodeURIComponent(p.player_id)}`}>{p.name}</Link>
                  {p.position && <span className="pos-tag">{p.position}</span>}
                </td>
                <td>{p.games}</td>
                <td>{p.pass_yds || '–'}</td><td>{p.pass_td || '–'}</td>
                <td>{p.rush_yds || '–'}</td><td>{p.rush_td || '–'}</td>
                <td>{p.receptions || '–'}</td><td>{p.targets || '–'}</td>
                <td>{p.rec_yds || '–'}</td><td>{p.rec_td || '–'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
