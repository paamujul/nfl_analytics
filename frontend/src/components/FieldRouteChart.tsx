// Field overlay of every target a player saw in one game, built from
// play-by-play (direction + air yards + catch point + YAC). Frequent and
// successful zones are highlighted; incompletions stay muted.
import type { RouteChart } from '../api';

const W = 560;
const H = 430;
const LOS_Y = 352;           // line of scrimmage
const PX_PER_YD = 7.6;
const LANE_X: Record<string, number> = { left: 132, middle: 280, right: 428 };
const LANE_W = 148;

function laneX(location: string | null, i: number): number {
  const base = LANE_X[location ?? 'middle'] ?? 280;
  // deterministic jitter so overlapping routes fan out
  const jitter = ((i * 53) % 100) / 100 - 0.5;
  return base + jitter * (LANE_W - 40);
}

function depthY(depth: number): number {
  return LOS_Y - Math.max(-6, Math.min(44, depth)) * PX_PER_YD;
}

export default function FieldRouteChart({ data }: { data: RouteChart }) {
  const routes = data.routes.filter((r) => r.location || r.depth != null);
  const maxZoneTargets = Math.max(1, ...data.zones.map((z) => z.targets));

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`Route chart: ${routes.length} targets`}
        style={{ width: '100%', maxWidth: 640, display: 'block', margin: '0 auto' }}>
        {/* field */}
        <rect x={40} y={8} width={W - 80} height={LOS_Y - 8} rx={10}
          fill="color-mix(in oklab, var(--series-3) 7%, var(--surface))"
          stroke="var(--grid)" />
        {/* yard lines every 10 */}
        {[0, 10, 20, 30, 40].map((yd) => (
          <g key={yd}>
            <line x1={42} x2={W - 42} y1={depthY(yd)} y2={depthY(yd)}
              stroke={yd === 0 ? 'var(--baseline)' : 'var(--grid)'}
              strokeWidth={yd === 0 ? 2 : 1} />
            <text x={W - 46} y={depthY(yd) - 4} textAnchor="end" fontSize={10}
              fill="var(--muted)">{yd === 0 ? 'LOS' : `+${yd}`}</text>
          </g>
        ))}
        {/* hash ticks */}
        {[1, 2, 3, 4].map((i) => LOS_Y - i * 5 * PX_PER_YD).map((y) => (
          <g key={y}>
            <line x1={225} x2={233} y1={y} y2={y} stroke="var(--grid)" />
            <line x1={327} x2={335} y1={y} y2={y} stroke="var(--grid)" />
          </g>
        ))}

        {/* zone highlights (frequency = fill strength; label = tgt + success) */}
        {data.zones.filter((z) => LANE_X[z.location]).map((z) => {
          const x = (LANE_X[z.location] ?? 280) - LANE_W / 2;
          const [d0, d1] = z.depth_band === 'deep' ? [15, 44] : [0, 15];
          const strength = (z.targets / maxZoneTargets) * 0.22;
          const good = z.success_rate >= 0.5;
          return (
            <g key={`${z.location}-${z.depth_band}`}>
              <rect x={x} y={depthY(d1)} width={LANE_W} height={depthY(d0) - depthY(d1)}
                rx={8} fill="var(--series-1)" opacity={strength}
                stroke={z.targets === maxZoneTargets ? 'var(--series-1)' : 'none'}
                strokeDasharray="4 3" />
              <text x={x + 8} y={depthY(d1) + 14} fontSize={10.5} fontWeight={700}
                fill={good ? 'var(--good)' : 'var(--muted)'}>
                {z.targets} tgt · {Math.round(z.success_rate * 100)}%
              </text>
            </g>
          );
        })}

        {/* routes */}
        {routes.map((r, i) => {
          const x = laneX(r.location, i);
          const depth = r.depth ?? (r.depth_band === 'deep' ? 22 : 6);
          const yCatch = depthY(depth);
          const color = r.touchdown ? 'var(--series-2)'
            : r.complete ? 'var(--series-1)' : 'var(--muted)';
          const bend = r.location === 'left' ? -26 : r.location === 'right' ? 26 : 0;
          const yacY = r.complete && r.yac ? Math.max(14, yCatch - r.yac * PX_PER_YD) : null;
          return (
            <g key={r.play_id} opacity={r.complete ? 0.85 : 0.45}>
              <path d={`M ${280} ${LOS_Y} Q ${x - bend} ${(LOS_Y + yCatch) / 2} ${x} ${yCatch}`}
                fill="none" stroke={color} strokeWidth={2} strokeLinecap="round"
                strokeDasharray={r.complete ? undefined : '5 4'} />
              {yacY != null && (
                <line x1={x} y1={yCatch} x2={x} y2={yacY} stroke={color}
                  strokeWidth={2} strokeDasharray="2 3" opacity={0.7} />
              )}
              <circle cx={x} cy={yCatch} r={r.touchdown ? 6 : 4.5} fill={color}
                stroke="var(--surface)" strokeWidth={2} />
              <title>
                {`Q${r.quarter ?? '?'} · ${r.complete ? `catch, ${r.yards ?? 0} yds` : 'incomplete'}`
                  + `${r.touchdown ? ' · TD' : ''}\n${r.desc ?? ''}`}
              </title>
            </g>
          );
        })}

        {/* legend */}
        <g fontSize={11} transform={`translate(46, ${H - 46})`}>
          <circle cx={6} cy={0} r={4.5} fill="var(--series-1)" />
          <text x={16} y={4} fill="var(--ink-2)">catch (line = path to catch point, dotted tail = YAC)</text>
          <circle cx={6} cy={20} r={4.5} fill="var(--muted)" />
          <text x={16} y={24} fill="var(--ink-2)">incomplete</text>
          <circle cx={286} cy={20} r={5.5} fill="var(--series-2)" />
          <text x={296} y={24} fill="var(--ink-2)">touchdown</text>
        </g>
      </svg>
      <p className="note" style={{ textAlign: 'center' }}>{data.tracking_note}</p>
    </div>
  );
}

export function RunDirectionChart({ data }: { data: RouteChart }) {
  const gaps = ['left end', 'left tackle', 'left guard', 'middle', 'right guard', 'right tackle', 'right end'];
  const byGap = new Map<string, { n: number; yds: number }>();
  for (const c of data.carries) {
    const key = c.gap === 'guard' && c.location === 'middle' ? 'middle'
      : c.gap === 'scramble' ? 'middle'
      : `${c.location ?? 'middle'} ${c.gap ?? ''}`.trim();
    const k = gaps.includes(key) ? key : 'middle';
    const cur = byGap.get(k) ?? { n: 0, yds: 0 };
    cur.n += 1; cur.yds += c.yards ?? 0;
    byGap.set(k, cur);
  }
  const max = Math.max(1, ...[...byGap.values()].map((v) => v.n));
  if (!data.carries.length) return null;
  return (
    <div style={{ marginTop: 10 }}>
      <h2 className="section">Run directions<small>{data.carries.length} carries</small></h2>
      <div className="card">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 8, alignItems: 'end', height: 120 }}>
          {gaps.map((g) => {
            const v = byGap.get(g);
            const h = v ? Math.max(8, (v.n / max) * 86) : 0;
            return (
              <div key={g} style={{ textAlign: 'center', fontSize: 11, color: 'var(--muted)' }}>
                <div title={v ? `${v.n} carries, ${v.yds.toFixed(0)} yds (${(v.yds / v.n).toFixed(1)}/carry)` : 'no carries'}
                  style={{ height: h, borderRadius: '4px 4px 0 0', background: 'var(--series-2)',
                           opacity: v ? 0.9 : 0, marginBottom: 4 }} />
                {v && <div style={{ fontWeight: 700, color: 'var(--ink)' }}>{(v.yds / v.n).toFixed(1)}</div>}
                {g.replace(' ', ' ')}
              </div>
            );
          })}
        </div>
        <p className="note" style={{ margin: '10px 0 0' }}>Bar height = carries to that gap; number = yards per carry.</p>
      </div>
    </div>
  );
}
