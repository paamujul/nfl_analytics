import { useEffect, useState } from 'react';
import { api } from '../api';
import type { DefenseProfile, LineupImpact, RosterPlayer, TeamTotals } from '../api';
import { PHASE_LABEL, useSeason } from '../state';

function PctBar({ label, value, pct, identityOnly, fmt }: {
  label: string; value: number | null; pct: number | null;
  identityOnly: boolean; fmt: (v: number | null) => string;
}) {
  const color = identityOnly ? 'var(--series-4)'
    : pct == null ? 'var(--muted)'
    : pct >= 65 ? 'var(--series-3)' : pct <= 35 ? 'var(--bad)' : 'var(--series-1)';
  return (
    <div className="pct-row">
      <span className="lbl">{label}</span>
      <div className="bar-track" style={{ height: 10 }}>
        <div className="bar-fill" style={{ width: `${pct ?? 0}%`, background: color }} />
      </div>
      <span className="val">{fmt(value)}{pct != null && <strong style={{ color: 'var(--ink)' }}> · p{pct}</strong>}</span>
    </div>
  );
}

const fmtPct = (v: number | null) => (v == null ? '–' : `${(v * 100).toFixed(1)}%`);
const fmt3 = (v: number | null) => (v == null ? '–' : v.toFixed(3));
const fmt1 = (v: number | null) => (v == null ? '–' : v.toFixed(1));
const FMT: Record<string, (v: number | null) => string> = {
  pass_epa_allowed: fmt3, rush_epa_allowed: fmt3, success_allowed: fmtPct,
  pass_yds_pg_allowed: fmt1, rush_yds_pg_allowed: fmt1,
  sack_rate: fmtPct, int_rate: fmtPct, explosive_pass_allowed: fmtPct,
  explosive_rush_allowed: fmtPct, stuff_rate: fmtPct, blitz_rate: fmtPct,
};

function SplitTable({ impact }: { impact: LineupImpact }) {
  const rows: { label: string; key: keyof LineupImpact['on']; fmt: (v: number | null) => string }[] = [
    { label: impact.method === 'play_level' ? 'Plays' : 'Plays (games)', key: 'plays', fmt: (v) => String(v ?? 0) },
    { label: 'Pass rate', key: 'pass_rate', fmt: fmtPct },
    { label: 'Pass yds / play', key: 'pass_yds_per_play', fmt: (v) => (v == null ? '–' : v.toFixed(2)) },
    { label: 'Rush yds / play', key: 'rush_yds_per_play', fmt: (v) => (v == null ? '–' : v.toFixed(2)) },
    { label: 'Pass EPA / play', key: 'pass_epa', fmt: fmt3 },
    { label: 'Rush EPA / play', key: 'rush_epa', fmt: fmt3 },
    { label: 'Success rate', key: 'success_rate', fmt: fmtPct },
  ];
  return (
    <table className="data" style={{ marginTop: 8 }}>
      <thead>
        <tr>
          <th>Metric</th>
          <th>All {impact.players.length} on field</th>
          <th>Not all on field</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            <td style={{ textAlign: 'left', color: 'var(--ink-2)' }}>{r.label}</td>
            <td style={{ fontWeight: 650 }}>{r.fmt(impact.on[r.key] as number | null)}</td>
            <td>{r.fmt(impact.off[r.key] as number | null)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function DefenseLabPage() {
  const { season, phase } = useSeason();
  const [teams, setTeams] = useState<TeamTotals[]>([]);
  const [team, setTeam] = useState('KC');
  const [profile, setProfile] = useState<DefenseProfile | null>(null);
  const [side, setSide] = useState<'defense' | 'offense'>('defense');
  const [roster, setRoster] = useState<RosterPlayer[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [impact, setImpact] = useState<LineupImpact | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.teams(season, phase).then((t) => setTeams(t.filter((x) => x.games > 0))).catch(() => {});
  }, [season, phase]);

  useEffect(() => {
    setProfile(null); setError(null);
    api.defense(team, season, phase).then(setProfile).catch((e) => setError(String(e)));
  }, [team, season, phase]);

  useEffect(() => {
    setRoster([]); setSelected([]); setImpact(null);
    api.roster(team, side, season, phase).then(setRoster).catch(() => {});
  }, [team, side, season, phase]);

  useEffect(() => {
    if (!selected.length) { setImpact(null); return; }
    setBusy(true);
    api.lineupImpact(team, side, selected, season, phase)
      .then(setImpact).catch((e) => setError(String(e)))
      .finally(() => setBusy(false));
  }, [team, side, selected, season, phase]);

  const toggle = (id: string) => setSelected((cur) =>
    cur.includes(id) ? cur.filter((x) => x !== id)
      : cur.length >= 6 ? cur : [...cur, id]);

  return (
    <>
      <div className="page-head">
        <h1>Defense lab</h1>
        <span className="sub">{season} {PHASE_LABEL[phase]?.toLowerCase()} · defensive identity + on-field player-combination impact</span>
      </div>

      <div className="controls">
        <select value={team} onChange={(e) => setTeam(e.target.value)} aria-label="Team">
          {teams.map((t) => <option key={t.abbr} value={t.abbr}>{t.name}</option>)}
        </select>
      </div>

      {error && <div className="error-box">{error}</div>}
      {profile && (
        <>
          <div className="verdict" style={{ borderLeftColor: profile.team.color ?? 'var(--series-1)' }}>
            <strong>{profile.headline}</strong>
            <div className="note" style={{ marginTop: 4 }}>
              Based on {profile.games} games. Percentiles are vs the rest of the league; longer bar = better defense
              (blitz rate is identity, shown in yellow).
            </div>
          </div>
          <div className="card">
            {profile.metrics.map((m) => (
              <PctBar key={m.key} label={m.label} value={m.value} pct={m.percentile}
                identityOnly={m.identity_only} fmt={FMT[m.key] ?? fmt3} />
            ))}
          </div>
        </>
      )}

      <h2 className="section">
        Lineup impact
        <small>pick up to 6 players — do opponents pass or run more (and better) when this exact group is on the field?</small>
      </h2>
      <div className="controls">
        <div className="seg" role="tablist">
          <button className={side === 'defense' ? 'on' : ''} onClick={() => setSide('defense')}>Defense</button>
          <button className={side === 'offense' ? 'on' : ''} onClick={() => setSide('offense')}>Offense</button>
        </div>
        <span className="note">
          {side === 'defense'
            ? 'How does this defensive group change what the opposing offense gains?'
            : 'With this offensive group on the field, does the offense lean pass or run?'}
        </span>
      </div>

      <div className="chip-row">
        {roster.slice(0, 28).map((p) => (
          <button key={p.id} className={`chip ${selected.includes(p.id) ? 'on' : ''}`}
            onClick={() => toggle(p.id)}>
            {p.name} <small>{p.position}{p.avg_snap_pct != null ? ` · ${Math.round(p.avg_snap_pct * 100)}% snaps` : ''}</small>
          </button>
        ))}
        {!roster.length && <span className="note">No roster data for this side/season yet.</span>}
      </div>

      {busy && <div className="loading">Crunching…</div>}
      {impact && !busy && (
        <div className="card" style={{ marginTop: 14 }}>
          <div className={`verdict ${impact.sufficient ? '' : 'warn'}`} style={{ margin: 0 }}>
            {impact.verdict}
            <div className="note" style={{ marginTop: 4 }}>
              {impact.method === 'play_level'
                ? 'Play-level analysis: exact on-field participation for every snap.'
                : 'Game-level approximation via snap counts (per-play participation is published after the season).'}
            </div>
          </div>
          <SplitTable impact={impact} />
        </div>
      )}
    </>
  );
}
