// Shared recharts tooltip styled to the design system.
export function VizTooltip({ active, payload, label }: {
  active?: boolean;
  payload?: { name: string; value: number | string; color?: string }[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="viz-tooltip">
      {label != null && <div className="t">{label}</div>}
      {payload.map((p) => (
        <div key={p.name} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 8, height: 8, borderRadius: 2, background: p.color ?? 'var(--series-1)' }} />
          <span style={{ color: 'var(--ink-2)' }}>{p.name}</span>
          <span style={{ fontWeight: 700, marginLeft: 'auto', paddingLeft: 10 }}>
            {typeof p.value === 'number' ? p.value.toLocaleString() : p.value}
          </span>
        </div>
      ))}
    </div>
  );
}
