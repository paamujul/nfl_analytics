import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { api } from '../api';
import type {
  CoachDefense, CoachDrives, CoachPlaybook, CoachUsage, MixEntry, ScriptSplit, Trajectory,
} from '../api';
import { PHASE_LABEL, useSeason } from '../state';
import { VizTooltip } from '../components/VizTooltip';
import {
  ChartedMetric, CoverageNote, MixBars, RankPill, coverageLevel, num, pct, signed,
} from '../components/ChartedMetric';
import { PlayCallerCard } from '../components/PlayCallerCard';

type Tab = 'playbook' | 'drives' | 'usage' | 'defense';
const TABS: { id: Tab; label: string; blurb: string }[] = [
  { id: 'playbook', label: 'Playbook', blurb: 'personnel, formation and tendency mix' },
  { id: 'drives', label: 'Drives', blurb: 'field position, outcomes and the opening script' },
  { id: 'usage', label: 'Usage', blurb: 'who gets the targets and carries, and when' },
  { id: 'defense', label: 'Defense', blurb: 'fronts, coverage shells and pressure' },
];

const SERIES = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)',
  'var(--good)', 'var(--bad)'];

const ZONE_LABEL: Record<string, string> = {
  own_1_20: 'Own 1–20', own_21_40: 'Own 21–40',
  midfield: 'Midfield', opp_territory: 'Opponent territory',
};

/** Trend arrow for a first-half → second-half share change. */
function Trend({ v }: { v: number | null }) {
  if (v == null) return null;
  const flat = Math.abs(v) < 0.01;
  return (
    <span className="trend" style={{ color: flat ? 'var(--muted)' : v > 0 ? 'var(--good)' : 'var(--bad)' }}>
      {flat ? '→' : v > 0 ? '↑' : '↓'} {signed(v * 100, 1)} pts
    </span>
  );
}

// ---------------------------------------------------------------- playbook

function BreakdownPicker({ personnel }: { personnel: CoachPlaybook['playbook']['personnel'] }) {
  const groups: { id: string; label: string; data: Record<string, MixEntry[]> }[] = [
    { id: 'down', label: 'By down', data: personnel.by_down },
    { id: 'distance', label: 'By distance', data: personnel.by_distance },
    { id: 'zone', label: 'By field zone', data: personnel.by_field_zone },
  ];
  const [which, setWhich] = useState('down');
  const active = groups.find((g) => g.id === which)!;
  const keys = Object.keys(active.data);
  if (!keys.length) return null;
  const label = (k: string) =>
    which === 'down' ? `${k}${['st', 'nd', 'rd', 'th'][Number(k) - 1] ?? 'th'} down`
      : which === 'zone' ? (ZONE_LABEL[k] ?? k.replace(/_/g, ' '))
        : k;
  return (
    <div className="card">
      <div className="controls" style={{ marginBottom: 10 }}>
        <div className="seg" role="tablist">
          {groups.map((g) => (
            <button key={g.id} className={which === g.id ? 'on' : ''}
              onClick={() => setWhich(g.id)}>{g.label}</button>
          ))}
        </div>
      </div>
      <div className="mix-grid">
        {keys.map((k) => (
          <MixBars key={k} title={label(k)} mix={active.data[k]} max={5} />
        ))}
      </div>
      <div className="note" style={{ marginTop: 8 }}>
        Personnel is derived from the play-by-play offense grouping (RB + TE count).
        Splits are counts within each bucket, so the n values are what matters at the tails.
      </div>
    </div>
  );
}

function PlaybookTab({ d }: { d: CoachPlaybook }) {
  const pb = d.playbook;
  const r = d.league_ranks;
  if (!pb.plays) {
    return <div className="verdict warn">No offensive plays recorded for {d.team.abbr} in{' '}
      {d.season} {PHASE_LABEL[d.phase]?.toLowerCase()}. Pick another season or phase above.</div>;
  }
  // 2021 has no FTN charting at all. The block is still present and so are the
  // four members -- each just comes back with n: 0 and its own `reason` string.
  // So "missing" is n === 0 across the board, not a null member; a null-member
  // test silently passes them through and they render as an empty sample
  // instead of "no such data". <ChartedMetric> prefers the API's own reason.
  const ftn = pb.ftn;
  const ftnMissing = !ftn || Object.values(ftn).every((v) => v == null || v.n === 0);
  const ftnNote = 'not charted before 2022 (FTN charting begins in the 2022 season)';

  return (
    <>
      <PlayCallerCard caller={d.play_caller} teamName={d.team.name} games={pb.games} />

      <div className="stat-row">
        <div className="card stat-tile">
          <div className="k">Plays</div><div className="v">{pb.plays.toLocaleString()}</div>
          <div className="u">{pb.games} games</div>
        </div>
        <div className="card stat-tile">
          <div className="k">Pass rate</div>
          <div className="v" style={{ color: 'var(--series-1)' }}>{pct(pb.pass_rate)}</div>
          <div className="u">every play counted · rank <RankPill rank={r.pass_rate} of={r.of} /></div>
        </div>
        <div className="card stat-tile">
          <div className="k">Run rate</div>
          <div className="v" style={{ color: 'var(--series-2)' }}>{pct(pb.run_rate)}</div>
          <div className="u">every play counted</div>
        </div>
        <div className="card stat-tile">
          <div className="k">EPA / play</div>
          <div className="v">{num(pb.epa_per_play, 3)}</div>
          <div className="u">rank <RankPill rank={r.epa_per_play} of={r.of} /></div>
        </div>
      </div>

      <h2 className="section">Tendencies
        <small>each figure carries the sample it was measured over</small></h2>
      <div className="card charted-grid">
        <ChartedMetric label="Expected pass rate (xpass)" c={pb.xpass}
          hint="model expectation given down, distance, score and clock" />
        <ChartedMetric label="Pass rate over expected" c={pb.pass_oe}
          fmt={(v) => (v == null ? '–' : `${v > 0 ? '+' : ''}${v.toFixed(2)}`)}
          hint={`rank ${r.pass_oe ?? '–'} of ${r.of}`} />
        <ChartedMetric label="Shotgun rate" c={pb.shotgun_rate}
          hint={`rank ${r.shotgun_rate ?? '–'} of ${r.of}`} />
        <ChartedMetric label="No-huddle rate" c={pb.no_huddle_rate}
          hint={`rank ${r.no_huddle_rate ?? '–'} of ${r.of}`} />
      </div>

      <h2 className="section">FTN charting
        <small>play-action, motion, screen and RPO come from FTN’s manual charting</small></h2>
      <div className="card charted-grid">
        <ChartedMetric label="Play action" c={ftn?.play_action}
          notCharted={ftnMissing ? ftnNote : undefined} />
        <ChartedMetric label="Pre-snap motion" c={ftn?.motion}
          notCharted={ftnMissing ? ftnNote : undefined} />
        <ChartedMetric label="Screen" c={ftn?.screen}
          notCharted={ftnMissing ? ftnNote : undefined} />
        <ChartedMetric label="RPO" c={ftn?.rpo}
          notCharted={ftnMissing ? ftnNote : undefined} />
      </div>
      {ftnMissing && (
        <div className="note" style={{ marginTop: -6 }}>
          These four are blank rather than zero: FTN charting starts in 2022, so
          {' '}{d.season} has no play-action, motion, screen or RPO record at all.
        </div>
      )}

      <h2 className="section">Personnel and formation<small>share of plays, with counts</small></h2>
      <div className="grid two">
        <div className="card">
          <MixBars title="Offensive personnel" mix={pb.personnel.mix} coverage={pb.personnel} />
          <div className="note" style={{ marginTop: 6 }}>
            First digit = running backs, second = tight ends (so “11” is 1 RB, 1 TE, 3 WR).
          </div>
        </div>
        <div className="card">
          <MixBars title="Formation" mix={pb.formation.mix} coverage={pb.formation}
            color="var(--series-2)" />
        </div>
      </div>

      <h2 className="section">Personnel by situation<small>the same mix, split by game state</small></h2>
      <BreakdownPicker personnel={pb.personnel} />
    </>
  );
}

// ------------------------------------------------------------------ drives

function ScriptColumn({ split, title, sub }: { split: ScriptSplit; title: string; sub: string }) {
  return (
    <div className="card">
      <div className="mix-title">{title}</div>
      <div className="note" style={{ marginBottom: 8 }}>{sub}</div>
      {!split.plays ? <div className="cov-note">no plays recorded</div> : (
        <>
          <div className="kv-rows">
            <div><span>Plays</span><b>{split.plays.toLocaleString()}</b></div>
            <div><span>Pass rate</span><b>{pct(split.pass_rate)}</b></div>
            <div><span>EPA / play</span><b>{num(split.epa_per_play, 3)}</b></div>
            <div><span>Success rate</span><b>{pct(split.success_rate)}</b></div>
          </div>
          <div className="charted-grid" style={{ marginTop: 10 }}>
            <ChartedMetric label="Shotgun" c={split.shotgun_rate} />
            <ChartedMetric label="Play action" c={split.play_action_rate}
              notCharted={split.play_action_rate?.value == null && split.plays
                ? 'not charted before 2022' : undefined} />
          </div>
          <MixBars title="Personnel" mix={split.personnel_mix} max={4} />
        </>
      )}
    </div>
  );
}

function DrivesTab({ d, scriptLength, setScriptLength }: {
  d: CoachDrives; scriptLength: number; setScriptLength: (n: number) => void;
}) {
  const dr = d.drives;
  const la = d.league_average;
  if (!dr.drives) {
    return <div className="verdict warn">No drives recorded for {d.team.abbr} in {d.season}{' '}
      {PHASE_LABEL[d.phase]?.toLowerCase()}.</div>;
  }
  const zoneChart = dr.by_start_zone.map((z) => ({
    name: ZONE_LABEL[z.zone] ?? z.zone,
    'Points / drive': z.points_per_drive ?? 0,
    'EPA / drive': z.epa_per_drive ?? 0,
    drives: z.drives,
  }));
  const vs = [
    { k: 'Avg start yardline', a: dr.avg_start_yardline, b: la.avg_start_yardline, fmt: (v: number | null) => num(v, 1) },
    { k: 'EPA / drive', a: dr.epa_per_drive, b: la.epa_per_drive, fmt: (v: number | null) => num(v, 3) },
    { k: 'Points / drive', a: dr.points_per_drive, b: la.points_per_drive, fmt: (v: number | null) => num(v, 2) },
    { k: 'Three-and-out rate', a: dr.three_and_out_rate, b: la.three_and_out_rate, fmt: (v: number | null) => pct(v) },
  ];

  return (
    <>
      <div className="stat-row">
        <div className="card stat-tile">
          <div className="k">Drives</div><div className="v">{dr.drives}</div>
        </div>
        {vs.map((m) => (
          <div className="card stat-tile" key={m.k}>
            <div className="k">{m.k}</div>
            <div className="v">{m.fmt(m.a)}</div>
            <div className="u">league {m.fmt(m.b)}
              {m.a != null && m.b != null && (
                <b style={{ marginLeft: 6, color: m.a >= m.b ? 'var(--good)' : 'var(--bad)' }}>
                  {/* format the gap the same way as the value it is a gap in,
                      so a percentage stays a percentage and 1.4 yards of field
                      position does not print as -1.372 */}
                  {m.a >= m.b ? '+' : '−'}{m.fmt(Math.abs(m.a - m.b))}
                </b>
              )}
            </div>
          </div>
        ))}
      </div>
      <div className="note" style={{ marginTop: -8, marginBottom: 14 }}>
        Every drive figure here is counted from the play-by-play, so these are complete
        counts rather than charted samples. “Better than league” is not signed for you on
        start yardline or three-and-out rate — read the direction yourself.
      </div>

      <div className="grid two">
        <div className="card">
          <h2 className="section" style={{ marginTop: 0 }}>Starting field position
            <small>what each starting zone was worth</small></h2>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={zoneChart} margin={{ top: 4, right: 8, left: -18, bottom: 0 }} barGap={2}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="name" tick={{ fill: 'var(--muted)', fontSize: 11 }}
                tickLine={false} axisLine={{ stroke: 'var(--baseline)' }} interval={0}
                angle={-20} height={46} textAnchor="end" />
              <YAxis tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip content={<VizTooltip />} cursor={{ fill: 'color-mix(in oklab, var(--series-1) 7%, transparent)' }} />
              <ReferenceLine y={0} stroke="var(--baseline)" />
              <Legend wrapperStyle={{ fontSize: 12.5 }} />
              <Bar dataKey="Points / drive" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={30} />
              <Bar dataKey="EPA / drive" fill="var(--series-3)" radius={[4, 4, 0, 0]} maxBarSize={30} />
            </BarChart>
          </ResponsiveContainer>
          <div className="mix-rows-tight">
            {dr.by_start_zone.map((z) => (
              <div className="mix-row" key={z.zone}>
                <span className="lbl">{ZONE_LABEL[z.zone] ?? z.zone}</span>
                <div className="bar-track" style={{ height: 9 }}>
                  <div className="bar-fill" style={{ width: `${(z.share ?? 0) * 100}%`, background: 'var(--series-4)' }} />
                </div>
                <span className="val">{pct(z.share, 1)}</span>
                <span className="cnt">n={z.drives}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <h2 className="section" style={{ marginTop: 0 }}>How drives ended
            <small>share of all {dr.drives} drives</small></h2>
          <MixBars mix={dr.results.map((x) => ({ key: x.result, n: x.n, rate: x.rate }))}
            max={12} color="var(--series-2)" />
        </div>
      </div>

      <h2 className="section">The opening script
        <small>are the first plays of each drive called differently from the rest?</small></h2>
      <div className="controls">
        <label className="note" htmlFor="script-len">Script length</label>
        <select id="script-len" value={scriptLength}
          onChange={(e) => setScriptLength(Number(e.target.value))}>
          {[5, 10, 15, 20, 25, 30].map((n) => <option key={n} value={n}>{n} plays</option>)}
        </select>
        <span className="note">
          the first {d.script.script_length} offensive plays of each game, versus everything after
        </span>
      </div>
      <div className="grid two">
        <ScriptColumn split={d.script.scripted} title={`Scripted (first ${d.script.script_length})`}
          sub="opening plays, typically prepared in advance" />
        <ScriptColumn split={d.script.rest_of_game} title="Rest of game"
          sub="everything called after the script runs out" />
      </div>

      {d.script.after_previous_drive.length > 0 && (
        <>
          <h2 className="section">Response to the previous drive
            <small>the opening play of a drive, by how the last one ended</small></h2>
          <div className="card" style={{ overflowX: 'auto' }}>
            <table className="data">
              <thead>
                <tr>
                  <th>Previous drive ended</th><th>Plays</th><th>Pass rate</th>
                  <th>EPA / play</th><th>Success</th><th>Shotgun</th>
                </tr>
              </thead>
              <tbody>
                {d.script.after_previous_drive.map((row) => {
                  const o = row.opening_play;
                  return (
                    <tr key={row.previous_result}>
                      <td>{row.previous_result}</td>
                      <td>{o.plays}</td>
                      <td>{pct(o.pass_rate)}</td>
                      <td>{num(o.epa_per_play, 3)}</td>
                      <td>{pct(o.success_rate)}</td>
                      <td>{pct(o.shotgun_rate?.value)}
                        {coverageLevel(o.shotgun_rate) !== 'full' && (
                          <small className="cov-inline"> ({Math.round((o.shotgun_rate?.coverage ?? 0) * 100)}% charted)</small>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="note" style={{ marginTop: 8 }}>
              Small buckets are common here — a “Safety” row can be one or two plays. The
              plays column is the sample; treat single-digit rows as anecdote.
            </div>
          </div>
        </>
      )}
    </>
  );
}

// ------------------------------------------------------------------- usage

type UsageMetric = 'target' | 'carry' | 'snap';
const USAGE_METRICS: { id: UsageMetric; label: string; key: keyof Pick<CoachUsage['players'][number], 'target_trajectory' | 'carry_trajectory' | 'snap_trajectory'> }[] = [
  { id: 'target', label: 'Target share', key: 'target_trajectory' },
  { id: 'carry', label: 'Carry share', key: 'carry_trajectory' },
  { id: 'snap', label: 'Snap share', key: 'snap_trajectory' },
];

function UsageTab({ d }: { d: CoachUsage }) {
  const [metric, setMetric] = useState<UsageMetric>('target');
  const [picked, setPicked] = useState<string | null>(null);
  const meta = USAGE_METRICS.find((m) => m.id === metric)!;

  const ranked = useMemo(() => {
    const key = metric === 'carry' ? 'carry_share' : metric === 'snap' ? 'snap_share' : 'target_share';
    return [...d.players].sort((a, b) => (b[key] ?? 0) - (a[key] ?? 0));
  }, [d.players, metric]);

  // Merge every charted player's weekly shares onto one week axis. Players
  // enter and leave the roster mid-season, so weeks are a ragged union and the
  // gaps stay as nulls rather than being drawn to zero.
  const { chartData, lines } = useMemo(() => {
    const top = ranked.filter((p) => (p[meta.key] as Trajectory | null)?.weeks?.length).slice(0, 6);
    const weeks = new Set<number>();
    top.forEach((p) => (p[meta.key] as Trajectory).weeks.forEach((w) => weeks.add(w.week)));
    const rows = [...weeks].sort((a, b) => a - b).map((wk) => {
      const row: Record<string, number | string | null> = { week: `W${wk}` };
      top.forEach((p) => {
        const hit = (p[meta.key] as Trajectory).weeks.find((w) => w.week === wk);
        row[p.name] = hit?.share != null ? Number((hit.share * 100).toFixed(1)) : null;
      });
      return row;
    });
    return { chartData: rows, lines: top.map((p) => p.name) };
  }, [ranked, meta.key]);

  const selected = picked ? d.players.find((p) => p.player_id === picked) ?? null : null;

  if (!d.players.length) {
    return <div className="verdict warn">No usage recorded for {d.team.abbr} in {d.season}{' '}
      {PHASE_LABEL[d.phase]?.toLowerCase()}.</div>;
  }

  return (
    <>
      <div className="stat-row">
        <div className="card stat-tile">
          <div className="k">Pass plays</div>
          <div className="v" style={{ color: 'var(--series-1)' }}>{d.team_pass_plays.toLocaleString()}</div>
        </div>
        <div className="card stat-tile">
          <div className="k">Run plays</div>
          <div className="v" style={{ color: 'var(--series-2)' }}>{d.team_run_plays.toLocaleString()}</div>
        </div>
        <div className="card stat-tile">
          <div className="k">Lead back</div>
          <div className="v" style={{ fontSize: 20 }}>{d.lead_back?.name ?? '–'}</div>
          <div className="u">
            {d.lead_back ? <>{pct(d.lead_back.carry_share)} of carries <Trend v={d.lead_back.trend} /></> : 'none identified'}
          </div>
        </div>
        <div className="card stat-tile">
          <div className="k">Backfield</div>
          <div className="v" style={{ fontSize: 20 }}>{d.backfield_is_committee ? 'Committee' : 'Bell cow'}</div>
          <div className="u">{d.primary_receivers.length} primary receiver{d.primary_receivers.length === 1 ? '' : 's'}</div>
        </div>
      </div>

      {!d.snap_counts_available && (
        <div className="verdict warn">
          <strong>No snap counts for this team-season</strong>
          <div className="note" style={{ marginTop: 4 }}>
            Target and carry shares below are still counted from the play-by-play, but snap
            share is unavailable, so nothing here says how often a player was on the field.
          </div>
        </div>
      )}

      <h2 className="section">Share over the season
        <small>top 6 by {meta.label.toLowerCase()}; gaps are weeks the player did not appear</small></h2>
      <div className="controls">
        <div className="seg" role="tablist">
          {USAGE_METRICS.map((m) => (
            <button key={m.id} className={metric === m.id ? 'on' : ''}
              onClick={() => setMetric(m.id)}
              disabled={m.id === 'snap' && !d.snap_counts_available}>{m.label}</button>
          ))}
        </div>
      </div>
      <div className="card">
        {chartData.length === 0 ? (
          <div className="cov-note">no weekly {meta.label.toLowerCase()} recorded</div>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={chartData} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="week" tick={{ fill: 'var(--muted)', fontSize: 11 }}
                tickLine={false} axisLine={{ stroke: 'var(--baseline)' }} />
              <YAxis tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false}
                axisLine={false} unit="%" />
              <Tooltip content={<VizTooltip />} />
              <Legend wrapperStyle={{ fontSize: 12.5 }} />
              {lines.map((name, i) => (
                <Line key={name} type="monotone" dataKey={name} stroke={SERIES[i % SERIES.length]}
                  strokeWidth={2} dot={{ r: 2 }} connectNulls={false} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>

      <h2 className="section">Every contributor<small>click a row for their route mix</small></h2>
      <div className="card" style={{ overflowX: 'auto', padding: '6px 12px' }}>
        <table className="data">
          <thead>
            <tr>
              <th>Player</th><th>Tgt</th><th>Tgt share</th><th>Trend</th>
              <th>Car</th><th>Car share</th><th>Trend</th><th>Snap share</th>
            </tr>
          </thead>
          <tbody>
            {ranked.map((p) => (
              <tr key={p.player_id} onClick={() => setPicked(p.player_id === picked ? null : p.player_id)}
                className={p.player_id === picked ? 'row-on' : ''} style={{ cursor: 'pointer' }}>
                <td>
                  <Link to={`/player/${encodeURIComponent(p.player_id)}`}
                    onClick={(e) => e.stopPropagation()}>{p.name}</Link>
                  {p.position && <span className="pos-tag">{p.position}</span>}
                </td>
                <td>{p.targets || '–'}</td>
                <td>{p.targets ? pct(p.target_share) : '–'}</td>
                <td><Trend v={p.target_trajectory?.trend ?? null} /></td>
                <td>{p.carries || '–'}</td>
                <td>{p.carries ? pct(p.carry_share) : '–'}</td>
                <td><Trend v={p.carry_trajectory?.trend ?? null} /></td>
                <td>
                  {p.snap_share == null
                    ? <span className="cov-note low" title="not matched to a snap-count row">unmatched</span>
                    : pct(p.snap_share)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <div className="card" style={{ marginTop: 14 }}>
          <div className="mix-title">{selected.name} — route mix</div>
          {selected.route_mix == null || !selected.route_mix.mix.length ? (
            <div className="cov-note">
              No routes charted for {selected.name}
              {selected.targets === 0 ? ' (no targets this season)' : ''}.
            </div>
          ) : (
            <>
              <MixBars mix={selected.route_mix.mix} coverage={selected.route_mix}
                unit="targets" max={10} color="var(--series-3)" />
              <div className="note" style={{ marginTop: 6 }}>
                Route names come from third-party charting keyed to targeted plays, so the
                denominator is this player’s charted targets, not their routes run.
              </div>
            </>
          )}
        </div>
      )}

      {d.snap_names_unresolved.length > 0 && (
        <div className="card" style={{ marginTop: 14 }}>
          <div className="mix-title">Unmatched snap-count names</div>
          <div className="note">
            {d.snap_names_unresolved.length} name{d.snap_names_unresolved.length === 1 ? '' : 's'} in
            the snap-count feed could not be joined to a play-by-play player id, so their snaps
            are missing from the shares above:{' '}
            <b>{d.snap_names_unresolved.join(', ')}</b>.
          </div>
        </div>
      )}
    </>
  );
}

// ----------------------------------------------------------------- defense

function DefenseTab({ d }: { d: CoachDefense }) {
  const df = d.defense;
  const r = d.league_ranks;
  if (!df.plays) {
    return <div className="verdict warn">No defensive plays recorded for {d.team.abbr} in{' '}
      {d.season} {PHASE_LABEL[d.phase]?.toLowerCase()}.</div>;
  }
  const manLow = coverageLevel(df.man_rate) === 'low' || coverageLevel(df.man_rate) === 'partial';
  const boxByDown = Object.entries(df.defenders_in_box.by_down ?? {})
    .map(([k, v]) => ({ name: `${k}${['st', 'nd', 'rd', 'th'][Number(k) - 1] ?? 'th'} down`,
      'Defenders in box': v.value ?? 0, n: v.n }));
  const rushers = d.players?.top_pass_rushers ?? [];

  return (
    <>
      <div className="stat-row">
        <div className="card stat-tile">
          <div className="k">Defensive plays</div>
          <div className="v">{df.plays.toLocaleString()}</div>
          <div className="u">{df.games} games</div>
        </div>
        <div className="card stat-tile">
          <div className="k">Pressure rate</div>
          <div className="v">{pct(df.pressure_rate.value)}</div>
          <div className="u">rank <RankPill rank={r.pressure_rate} of={r.of} /> ·{' '}
            <CoverageNote c={df.pressure_rate} unit="dropbacks" always /></div>
        </div>
        <div className="card stat-tile">
          <div className="k">Blitz rate</div>
          <div className="v">{pct(df.blitz_rate.value)}</div>
          <div className="u">rank <RankPill rank={r.blitz_rate} of={r.of} /> ·{' '}
            <CoverageNote c={df.blitz_rate} unit="dropbacks" always /></div>
        </div>
        <div className="card stat-tile">
          <div className="k">Defenders in box</div>
          <div className="v">{num(df.defenders_in_box.value, 2)}</div>
          <div className="u">rank <RankPill rank={r.defenders_in_box} of={r.of} /> ·{' '}
            <CoverageNote c={df.defenders_in_box} always /></div>
        </div>
      </div>

      <h2 className="section">Coverage shell
        <small>the thinnest sample on this page — read the caption before the number</small></h2>
      <div className={`card ${manLow ? 'low-cov-card' : ''}`}>
        <div className="verdict warn" style={{ margin: '0 0 12px' }}>
          <strong>Man/zone is charted on a partial sample</strong>
          <div className="note" style={{ marginTop: 4 }}>
            The coverage column is present on{' '}
            <b>{df.man_rate.coverage == null ? '–' : `${Math.round(df.man_rate.coverage * 100)}%`}</b>{' '}
            of this team’s dropbacks ({df.man_rate.n.toLocaleString()} plays). The split below is
            therefore the tendency <em>among charted plays</em>. It equals the true tendency only
            if charting is independent of what was called, which has not been established — so
            it does not carry the same weight as pressure rate or the personnel mix.
          </div>
        </div>
        <div className="charted-grid">
          <ChartedMetric label="Man coverage" c={df.man_rate} unit="dropbacks"
            hint={`rank ${r.man_rate ?? '–'} of ${r.of}`} />
          <ChartedMetric label="Zone coverage" c={df.zone_rate} unit="dropbacks" />
        </div>
        <MixBars title="Coverage families" mix={df.coverage_types.mix}
          coverage={df.coverage_types} unit="dropbacks" max={10} color="var(--series-4)" />
      </div>

      <h2 className="section">Fronts and packages<small>share of defensive snaps</small></h2>
      <div className="grid two">
        <div className="card">
          <MixBars title="Personnel (DL–LB–DB)" mix={df.personnel.mix} coverage={df.personnel} />
        </div>
        <div className="card">
          <MixBars title="Package" mix={df.personnel.packages} coverage={df.personnel}
            color="var(--series-3)" />
        </div>
      </div>

      {boxByDown.length > 0 && (
        <>
          <h2 className="section">Box count by down<small>average defenders in the box</small></h2>
          <div className="card">
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={boxByDown} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="name" tick={{ fill: 'var(--muted)', fontSize: 11 }}
                  tickLine={false} axisLine={{ stroke: 'var(--baseline)' }} />
                <YAxis domain={[4, 9]} tick={{ fill: 'var(--muted)', fontSize: 11 }}
                  tickLine={false} axisLine={false} />
                <Tooltip content={<VizTooltip />} cursor={{ fill: 'color-mix(in oklab, var(--series-1) 7%, transparent)' }} />
                <Bar dataKey="Defenders in box" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={44} />
              </BarChart>
            </ResponsiveContainer>
            <div className="note">
              {boxByDown.map((b) => `${b.name}: n=${b.n}`).join(' · ')}
            </div>
          </div>
        </>
      )}

      {Object.keys(d.starters).length > 0 && (
        <>
          <h2 className="section">Regular starters<small>weeks listed as a starter</small></h2>
          <div className="grid two">
            {Object.entries(d.starters).map(([group, players]) => (
              <div className="card" key={group}>
                <div className="mix-title">{group}</div>
                <div className="chip-row">
                  {/* a player can appear twice in one group with different
                      listed positions (BAL 2024 has Travis Jones as both DT
                      and NT), so player_id alone is not a unique key */}
                  {players.map((p, i) => (
                    <Link className="chip" key={`${p.player_id}-${p.position}-${i}`}
                      to={`/player/${encodeURIComponent(p.player_id)}`}>
                      {p.name} <small>{p.position} · {p.weeks_as_starter}w</small>
                    </Link>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {rushers.length > 0 && (
        <>
          <h2 className="section">Pass rush
            <small>{d.players?.source}</small></h2>
          <div className="card" style={{ overflowX: 'auto', padding: '6px 12px' }}>
            <table className="data">
              <thead>
                <tr><th>Player</th><th>G</th><th>Pressures</th><th>Per game</th>
                  <th>Sacks</th><th>Knockdowns</th><th>Missed tkl</th></tr>
              </thead>
              <tbody>
                {rushers.map((p) => (
                  <tr key={p.player_id}>
                    <td>
                      <Link to={`/player/${encodeURIComponent(p.player_id)}`}>{p.name}</Link>
                      {p.position && <span className="pos-tag">{p.position}</span>}
                      {p.small_sample && <span className="badge info">small sample</span>}
                    </td>
                    <td>{p.games}</td>
                    <td>{num(p.pressures, 0)}</td>
                    <td>{num(p.pressures_per_game, 2)}</td>
                    <td>{num(p.sacks, 0)}</td>
                    <td>{num(p.qb_knockdowns, 0)}</td>
                    <td>{pct(p.missed_tackle_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="note" style={{ marginTop: 8 }}>
              These rows are season totals from a separate source, not per-play data, so they
              cannot be split by phase or filtered to the games above.
            </div>
          </div>
        </>
      )}
    </>
  );
}

// -------------------------------------------------------------------- page

export default function CoachPage() {
  const { abbr = '' } = useParams();
  const { season, phase } = useSeason();
  const [tab, setTab] = useState<Tab>('playbook');
  const [scriptLength, setScriptLength] = useState(15);

  const [playbook, setPlaybook] = useState<CoachPlaybook | null>(null);
  const [drives, setDrives] = useState<CoachDrives | null>(null);
  const [usage, setUsage] = useState<CoachUsage | null>(null);
  const [defense, setDefense] = useState<CoachDefense | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // The playbook call also supplies the team header, so it gates the page;
  // the other three load alongside it and each section handles its own gap.
  useEffect(() => {
    let stale = false;
    setPlaybook(null); setDrives(null); setUsage(null); setDefense(null);
    setError(null); setLoading(true);
    Promise.allSettled([
      api.coachPlaybook(abbr, season, phase),
      api.coachUsage(abbr, season, phase),
      api.coachDefense(abbr, season, phase),
    ]).then(([pb, us, df]) => {
      if (stale) return;
      if (pb.status === 'fulfilled') setPlaybook(pb.value);
      else setError(String(pb.reason));
      if (us.status === 'fulfilled') setUsage(us.value);
      if (df.status === 'fulfilled') setDefense(df.value);
      setLoading(false);
    });
    return () => { stale = true; };
  }, [abbr, season, phase]);

  // script_length is a query parameter, so drives refetches when it changes
  useEffect(() => {
    let stale = false;
    setDrives(null);
    api.coachDrives(abbr, season, phase, scriptLength)
      .then((d) => { if (!stale) setDrives(d); })
      .catch(() => { if (!stale) setDrives(null); });
    return () => { stale = true; };
  }, [abbr, season, phase, scriptLength]);

  if (error) return <div className="error-box">Couldn’t load the coaching profile: {error}</div>;
  if (loading) return <div className="loading">Loading {abbr} coaching profile…</div>;

  const team = playbook?.team;
  const active = TABS.find((t) => t.id === tab)!;

  return (
    <>
      <div className="page-head">
        {team?.logo && <img src={team.logo} alt="" />}
        <span className="accent-chip" style={{ background: team?.color ?? 'var(--series-1)' }} />
        <div>
          <h1>{team?.name ?? abbr} — coaching profile</h1>
          <span className="sub">
            {season} {PHASE_LABEL[phase]?.toLowerCase()} ·{' '}
            <Link to={`/team/${abbr}`} style={{ color: 'var(--series-1)', fontWeight: 600 }}>
              team page →
            </Link>
          </span>
        </div>
      </div>

      <div className="controls">
        <div className="seg" role="tablist">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id}
              className={tab === t.id ? 'on' : ''} onClick={() => setTab(t.id)}>{t.label}</button>
          ))}
        </div>
        <span className="note">{active.blurb}</span>
      </div>

      {tab === 'playbook' && (playbook
        ? <PlaybookTab d={playbook} />
        : <div className="error-box">Playbook unavailable for this team-season.</div>)}

      {tab === 'drives' && (drives
        ? <DrivesTab d={drives} scriptLength={scriptLength} setScriptLength={setScriptLength} />
        : <div className="loading">Loading drives…</div>)}

      {tab === 'usage' && (usage
        ? <UsageTab d={usage} />
        : <div className="error-box">Usage unavailable for this team-season.</div>)}

      {tab === 'defense' && (defense
        ? <DefenseTab d={defense} />
        : <div className="error-box">Defensive profile unavailable for this team-season.</div>)}

      <div className="note" style={{ marginTop: 28, borderTop: '1px solid var(--grid)', paddingTop: 12 }}>
        Play-caller attribution on this page is model-generated and unverified. Figures marked
        with a charted-sample caption were measured over the subset of plays a third party
        recorded the column on, not over every play.
      </div>
    </>
  );
}
