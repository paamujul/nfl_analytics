// Rendering primitives for the {value, n, coverage} shape the coaching API
// returns for every charted rate.
//
// Why this file exists: roughly half of the columns behind these numbers are
// third-party charting that is NOT recorded on every play. In this database
// man/zone coverage is charted on 47-69% of dropbacks depending on the season,
// and a player's route mix can be charted on anywhere from 0% to 100% of their
// targets. A man/zone split measured over 55% of plays is a tendency *among
// charted plays*; it equals the real tendency only if charting is random with
// respect to the play, and nobody has established that it is.
//
// So the rule enforced here is: a charted rate is never rendered as a bare
// number. Every one of them carries its denominator, and anything under
// LOW_COVERAGE is additionally de-emphasised so it cannot be read with the
// same confidence as a 100%-coverage rate like pass_rate.

import type { Charted, Mix, MixEntry } from '../api';

// Below this fraction a rate is visually downgraded, not just annotated.
export const LOW_COVERAGE = 0.5;
// Charting pipelines drop a handful of plays even when they are "complete";
// treat anything at or above this as full coverage and skip the caption.
const FULL_COVERAGE = 0.995;

export const pct = (v: number | null | undefined, digits = 1) =>
  v == null ? '–' : `${(v * 100).toFixed(digits)}%`;
export const num = (v: number | null | undefined, digits = 2) =>
  v == null ? '–' : v.toFixed(digits);
export const signed = (v: number | null | undefined, digits = 2) =>
  v == null ? '–' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}`;

export type CoverageLevel = 'none' | 'low' | 'partial' | 'full';

export function coverageLevel(c: Charted | Mix | null | undefined): CoverageLevel {
  if (!c || c.coverage == null || c.n === 0) return 'none';
  // a coverage figure can land marginally above 1 when the charted denominator
  // and the play denominator are counted slightly differently upstream; clamp
  if (c.coverage >= FULL_COVERAGE) return 'full';
  return c.coverage < LOW_COVERAGE ? 'low' : 'partial';
}

/** The muted "x of y plays charted" line. Renders nothing at full coverage. */
export function CoverageNote({ c, unit = 'plays', always = false }: {
  c: Charted | Mix | null | undefined; unit?: string; always?: boolean;
}) {
  const level = coverageLevel(c);
  if (level === 'none') return <span className="cov-note">no {unit} recorded</span>;
  if (level === 'full') {
    return always
      ? <span className="cov-note">n = {c!.n.toLocaleString()} {unit} · fully recorded</span>
      : null;
  }
  const shown = Math.min(c!.coverage!, 1);
  return (
    <span className={`cov-note ${level === 'low' ? 'low' : ''}`}>
      n = {c!.n.toLocaleString()} · {Math.round(shown * 100)}% of {unit} charted
      {level === 'low' && ' — read as a tendency among charted plays only'}
    </span>
  );
}

/**
 * A single charted rate: label, value, and its provenance. `notCharted`
 * renders the explicit "not collected in this season" state — used for FTN
 * fields before 2022, which come back as null rather than 0.
 */
export function ChartedMetric({ label, c, fmt = pct, unit = 'plays', notCharted, hint }: {
  label: string;
  c: Charted | null | undefined;
  fmt?: (v: number | null) => string;
  unit?: string;
  notCharted?: string;
  hint?: string;
}) {
  // The API attaches `reason` to a metric whose column does not exist for the
  // season at all (the FTN fields do this for 2021). That is a different state
  // from "sparsely charted" and outranks any caller-supplied text: it is the
  // difference between 0% and no measurement, and must never read as a zero.
  const why = c?.reason || notCharted;
  if (c == null || why) {
    return (
      <div className="charted uncharted">
        <div className="k">{label}</div>
        <div className="v">–</div>
        <div className="cov-note low">{why ?? 'not collected'}</div>
      </div>
    );
  }
  const level = coverageLevel(c);
  return (
    <div className={`charted lvl-${level}`}>
      <div className="k">{label}</div>
      <div className="v">{fmt(c.value)}</div>
      {hint && <div className="cov-hint">{hint}</div>}
      <CoverageNote c={c} unit={unit} always />
    </div>
  );
}

/** A rank-of-32 pill. Lower is "more" of the thing, per the API's ordering. */
export function RankPill({ rank, of }: { rank: number | null; of: number }) {
  if (rank == null || !of) return null;
  const tone = rank <= of / 3 ? 'hi' : rank > (2 * of) / 3 ? 'lo' : '';
  return <span className={`rank-pill ${tone}`}>{rank}<small>/{of}</small></span>;
}

/**
 * Personnel/formation/coverage mix as labelled bars. Each row keeps its own n
 * next to the rate, and the block footer carries the coverage of the whole mix
 * — a mix charted on 55% of plays is captioned and dimmed as a unit.
 */
export function MixBars({ title, mix, coverage, unit = 'plays', color = 'var(--series-1)', max = 8, empty = 'no plays recorded' }: {
  title?: string;
  mix: MixEntry[] | null | undefined;
  coverage?: Charted | Mix | null;
  unit?: string;
  color?: string;
  max?: number;
  empty?: string;
}) {
  const rows = (mix ?? []).slice(0, max);
  const level = coverageLevel(coverage);
  const top = rows.reduce((m, r) => Math.max(m, r.rate ?? 0), 0) || 1;
  return (
    <div className={`mix-block ${level === 'low' ? 'low-cov' : ''}`}>
      {title && <div className="mix-title">{title}</div>}
      {rows.length === 0 && <div className="cov-note">{empty}</div>}
      {rows.map((r) => (
        <div className="mix-row" key={r.key}>
          <span className="lbl" title={r.key}>{r.key}</span>
          <div className="bar-track" style={{ height: 9 }}>
            <div className="bar-fill" style={{ width: `${((r.rate ?? 0) / top) * 100}%`, background: color }} />
          </div>
          <span className="val">{pct(r.rate, 1)}</span>
          <span className="cnt">n={r.n}</span>
        </div>
      ))}
      {coverage !== undefined && rows.length > 0 && (
        <div className="mix-foot"><CoverageNote c={coverage} unit={unit} always /></div>
      )}
    </div>
  );
}
