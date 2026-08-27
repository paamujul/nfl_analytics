import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { api } from '../api';
import type { PlayerQuarters, RouteChart } from '../api';
import { PHASE_LABEL, useSeason } from '../state';
import FieldRouteChart, { RunDirectionChart } from '../components/FieldRouteChart';
import { VizTooltip } from '../components/VizTooltip';

const ORDINALS: Record<number, string> = { 1: '1st', 2: '2nd', 3: '3rd', 4: '4th', 5: 'OT' };

export default function PlayerPage() {
  const { id = '' } = useParams();
  const { season, phase } = useSeason();
  const [game, setGame] = useState<string>('');            // '' = whole phase
  const [data, setData] = useState<PlayerQuarters | null>(null);
  const [routes, setRoutes] = useState<RouteChart | null>(null);
  const [routeGame, setRouteGame] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { setGame(''); }, [id, season, phase]);

  useEffect(() => {
    setData(null); setError(null);
    api.playerQuarters(id, season, phase, game || null)
      .then(setData).catch((e) => setError(String(e)));
  }, [id, season, phase, game]);

  // route chart needs a single game: follow the selector, else latest game
  useEffect(() => {
    const target = game || (data?.games.length ? data.games[data.games.length - 1].game_id : '');
    if (!target) { setRoutes(null); return; }
    setRouteGame(target);
    api.playerRoutes(id, target).then(setRoutes).catch(() => setRoutes(null));
  }, [id, game, data]);

  const chart = useMemo(() => (data?.quarters ?? [])
    .filter((q) => q.quarter >= 1 && q.quarter <= 5)
    .map((q) => ({
      name: ORDINALS[q.quarter] ?? `Q${q.quarter}`,
      Passing: q.pass_yds, Rushing: q.rush_yds, Receiving: q.rec_yds,
    })), [data]);

  if (error) return <div className="error-box">Couldn’t load player: {error}</div>;
  if (!data) return <div className="loading">Loading player…</div>;

  const p = data.player;
  const hasPass = data.quarters.some((q) => q.pass_att > 0);
  const hasRush = data.quarters.some((q) => q.rush_att > 0);
  const hasRec = data.quarters.some((q) => q.targets > 0);
  const routeGameInfo = data.games.find((g) => g.game_id === routeGame);

  return (
    <>
      <div className="page-head">
        {p.headshot && <img src={p.headshot} alt="" style={{ borderRadius: '50%', objectFit: 'cover' }} />}
        <div>
          <h1>{p.name} {p.position && <span className="pos-tag" style={{ fontSize: 13 }}>{p.position}</span>}</h1>
          <span className="sub">
            {p.team && <Link to={`/team/${p.team}`} style={{ fontWeight: 600 }}>{p.team}</Link>}
            {' '}· {season} {PHASE_LABEL[phase]?.toLowerCase()}
          </span>
        </div>
      </div>

      <div className="controls">
        <select value={game} onChange={(e) => setGame(e.target.value)} aria-label="Game">
          <option value="">All games ({data.games.length})</option>
          {data.games.map((g) => (
            <option key={g.game_id} value={g.game_id}>
              Week {g.week}: {g.away} @ {g.home}
            </option>
          ))}
        </select>
        {data.best_quarter && (
          <span className="badge info" style={{ fontSize: 12.5, padding: '5px 10px', textTransform: 'none' }}>
            Best quarter: <strong>{ORDINALS[data.best_quarter]}</strong> (most total yards{game ? ' this game' : ''})
          </span>
        )}
      </div>

      {chart.length > 0 ? (
        <div className="card">
          <h2 className="section" style={{ marginTop: 0 }}>Yards by quarter</h2>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={chart} margin={{ top: 4, right: 8, left: -18, bottom: 0 }} barGap={2}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="name" tick={{ fill: 'var(--muted)', fontSize: 12 }}
                tickLine={false} axisLine={{ stroke: 'var(--baseline)' }} />
              <YAxis tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip content={<VizTooltip />} cursor={{ fill: 'color-mix(in oklab, var(--series-1) 7%, transparent)' }} />
              <Legend wrapperStyle={{ fontSize: 12.5 }} />
              {hasPass && <Bar dataKey="Passing" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={34} />}
              {hasRush && <Bar dataKey="Rushing" fill="var(--series-2)" radius={[4, 4, 0, 0]} maxBarSize={34} />}
              {hasRec && <Bar dataKey="Receiving" fill="var(--series-3)" radius={[4, 4, 0, 0]} maxBarSize={34} />}
            </BarChart>
          </ResponsiveContainer>
        </div>
      ) : <div className="loading">No plays recorded for this selection.</div>}

      {data.quarters.length > 0 && (
        <div className="card" style={{ marginTop: 14, overflowX: 'auto', padding: '6px 12px' }}>
          <table className="data">
            <thead>
              <tr>
                <th>Quarter</th><th>Plays</th>
                {hasPass && <><th>Cmp/Att</th><th>Pass yds</th><th>Pass TD</th></>}
                {hasRush && <><th>Car</th><th>Rush yds</th></>}
                {hasRec && <><th>Rec/Tgt</th><th>Rec yds</th></>}
                <th>EPA/play</th><th>Success %</th>
              </tr>
            </thead>
            <tbody>
              {data.quarters.filter((q) => q.quarter >= 1).map((q) => (
                <tr key={q.quarter} style={q.quarter === data.best_quarter
                  ? { background: 'color-mix(in oklab, var(--series-3) 10%, transparent)' } : undefined}>
                  <td style={{ fontWeight: 700 }}>{ORDINALS[q.quarter] ?? q.quarter}</td>
                  <td>{q.plays}</td>
                  {hasPass && <><td>{q.pass_cmp}/{q.pass_att}</td><td>{q.pass_yds}</td><td>{q.pass_td}</td></>}
                  {hasRush && <><td>{q.rush_att}</td><td>{q.rush_yds}</td></>}
                  {hasRec && <><td>{q.receptions}/{q.targets}</td><td>{q.rec_yds}</td></>}
                  <td>{q.epa_per_play ?? '–'}</td>
                  <td>{q.success_rate != null ? `${Math.round(q.success_rate * 100)}%` : '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {routes && routes.routes.length > 0 && (
        <>
          <h2 className="section">
            Route chart
            <small>
              {routeGameInfo ? `week ${routeGameInfo.week}: ${routeGameInfo.away} @ ${routeGameInfo.home}` : ''}
              {' '}· all {routes.routes.length} targets overlaid
            </small>
          </h2>
          <div className="card"><FieldRouteChart data={routes} /></div>
        </>
      )}
      {routes && <RunDirectionChart data={routes} />}
    </>
  );
}
