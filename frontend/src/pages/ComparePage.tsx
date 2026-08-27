import { useEffect, useState } from 'react';
import { api } from '../api';
import type { CompareData, CompareMetricSide, TeamTotals } from '../api';
import { PHASE_LABEL, useSeason } from '../state';

function DuelRow({ label, side, colorA, colorB, fmt }: {
  label: string; side: CompareMetricSide; colorA: string; colorB: string;
  fmt: (v: number | null) => string;
}) {
  return (
    <div className="duel-row">
      <span className="n left">{fmt(side.a)}</span>
      <div className="duel-track left">
        <div className="fill" style={{ width: `${side.a_pct ?? 0}%`, background: colorA }} />
      </div>
      <span className="m">{label}{!side.neutral && <span className="note"> · league pct</span>}</span>
      <div className="duel-track right">
        <div className="fill" style={{ width: `${side.b_pct ?? 0}%`, background: colorB }} />
      </div>
      <span className="n">{fmt(side.b)}</span>
    </div>
  );
}

const fmtNum = (v: number | null) => (v == null ? '–' : v.toLocaleString());
const fmtPct = (v: number | null) => (v == null ? '–' : `${Math.round(v * 100)}%`);
const fmt3 = (v: number | null) => (v == null ? '–' : v.toFixed(3));

const FMT: Record<string, (v: number | null) => string> = {
  pass_yds_pg: fmtNum, rush_yds_pg: fmtNum, yds_per_play: fmtNum,
  pass_epa: fmt3, rush_epa: fmt3,
  success_rate: fmtPct, explosive_rate: fmtPct, pass_rate: fmtPct,
};

export default function ComparePage() {
  const { season, phase } = useSeason();
  const [teams, setTeams] = useState<TeamTotals[]>([]);
  const [a, setA] = useState('KC');
  const [b, setB] = useState('BUF');
  const [data, setData] = useState<CompareData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.teams(season, phase).then((t) => setTeams(t.filter((x) => x.games > 0))).catch(() => {});
  }, [season, phase]);

  useEffect(() => {
    setData(null); setError(null);
    api.compare(a, b, season, phase).then(setData).catch((e) => setError(String(e)));
  }, [a, b, season, phase]);

  const colorA = data?.a.color ?? 'var(--series-1)';
  const colorB = data?.b.color ?? 'var(--series-2)';

  return (
    <>
      <div className="page-head">
        <h1>Compare teams</h1>
        <span className="sub">{season} {PHASE_LABEL[phase]?.toLowerCase()} · bars show league percentile (longer = better, except pass rate which is tendency)</span>
      </div>

      <div className="vs-head card">
        <div className="vs-team">
          {data?.a.logo && <img src={data.a.logo} alt="" />}
          <select value={a} onChange={(e) => setA(e.target.value)} aria-label="Team A">
            {teams.map((t) => <option key={t.abbr} value={t.abbr}>{t.name}</option>)}
          </select>
        </div>
        <span className="vs-x">VS</span>
        <div className="vs-team right">
          {data?.b.logo && <img src={data.b.logo} alt="" />}
          <select value={b} onChange={(e) => setB(e.target.value)} aria-label="Team B">
            {teams.map((t) => <option key={t.abbr} value={t.abbr}>{t.name}</option>)}
          </select>
        </div>
      </div>

      {error && <div className="error-box">{error}</div>}
      {!data && !error && <div className="loading">Comparing…</div>}
      {data && (
        <>
          <h2 className="section">Offense</h2>
          <div className="card">
            {data.metrics.map((m) => (
              <DuelRow key={m.key} label={m.label} side={m.offense}
                colorA={colorA} colorB={colorB} fmt={FMT[m.key] ?? fmtNum} />
            ))}
          </div>
          <h2 className="section">Defense<small>what each team allows — longer bar still means better defense</small></h2>
          <div className="card">
            {data.metrics.map((m) => (
              <DuelRow key={m.key} label={m.label} side={m.defense}
                colorA={colorA} colorB={colorB} fmt={FMT[m.key] ?? fmtNum} />
            ))}
          </div>
        </>
      )}
    </>
  );
}
