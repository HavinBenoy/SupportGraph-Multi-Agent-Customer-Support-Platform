/** Small accessible horizontal bar list (plain HTML/CSS, no chart library). Each bar also states its value as text. */
export function Bars({ title, data, unit = "" }: { title: string; data: Record<string, number>; unit?: string }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return (
    <figure className="card bars">
      <figcaption>{title}</figcaption>
      {entries.length === 0 ? <p className="muted">No data yet.</p> : (
        <ul>
          {entries.map(([k, v]) => (
            <li key={k}><span className="k">{k.replace(/_/g, " ")}</span>
              <span className="track" aria-hidden="true"><span className="fill" style={{ width: `${(v / max) * 100}%` }} /></span>
              <span className="v">{v}{unit}</span></li>
          ))}
        </ul>
      )}
    </figure>
  );
}

export function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return <div className="card stat"><div className="num">{value}</div><div className="lbl">{label}</div>{hint && <div className="muted tiny">{hint}</div>}</div>;
}

/** Turns-per-day column chart as inline SVG with a text alternative. */
export function DailyChart({ series }: { series: { date: string; turns: number }[] }) {
  const max = Math.max(1, ...series.map((s) => s.turns));
  const w = 28, h = 90;
  return (
    <figure className="card">
      <figcaption>Chat turns per day</figcaption>
      <svg role="img" aria-label={`Chat turns per day: ${series.map((s) => `${s.date} ${s.turns}`).join(", ")}`} viewBox={`0 0 ${series.length * w} ${h + 16}`} className="daily">
        {series.map((s, i) => {
          const bh = (s.turns / max) * h;
          return <g key={s.date}><rect x={i * w + 4} y={h - bh} width={w - 8} height={bh} rx="2" className="col"><title>{`${s.date}: ${s.turns}`}</title></rect>
            {(i % Math.ceil(series.length / 7) === 0) && <text x={i * w + w / 2} y={h + 12} textAnchor="middle" className="axis">{s.date.slice(5)}</text>}</g>;
        })}
      </svg>
    </figure>
  );
}
