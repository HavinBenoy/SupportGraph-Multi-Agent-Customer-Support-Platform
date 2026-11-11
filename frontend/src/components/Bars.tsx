/** Small accessible horizontal bar list (plain HTML/CSS, no chart library). Each bar also states its value as text. */
export function Bars({ title, data, unit = "", hint, keepOrder = false }: { title: string; data: Record<string, number>; unit?: string; hint?: string; keepOrder?: boolean }) {
  const entries = keepOrder ? Object.entries(data) : Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return (
    <figure className="card bars">
      <figcaption>{title}</figcaption>
      {hint && <p className="muted tiny fig-hint">{hint}</p>}
      {entries.length === 0 || entries.every(([, v]) => v === 0) ? <p className="muted">No data yet.</p> : (
        <ul>
          {entries.map(([k, v]) => (
            <li key={k} title={`${k.replace(/_/g, " ")}: ${v}${unit}`}><span className="k">{k.replace(/_/g, " ")}</span>
              <span className="track" aria-hidden="true"><span className="fill" style={{ width: `${(v / max) * 100}%` }} /></span>
              <span className="v">{v}{unit}</span></li>
          ))}
        </ul>
      )}
    </figure>
  );
}

export function Stat({ label, value, hint, def }: { label: string; value: string | number; hint?: string; def?: string }) {
  return <div className="card stat" title={def}><div className="num">{value}</div><div className="lbl">{label}</div>{hint && <div className="muted tiny">{hint}</div>}</div>;
}

/** Request -> publication funnel: one bar per step, each stating its count and the share of the step before (single hue: the steps are one measure). */
export function Funnel({ title, steps }: { title: string; steps: { step: string; count: number }[] }) {
  const top = Math.max(1, steps[0]?.count ?? 0);
  return (
    <figure className="card bars funnel">
      <figcaption>{title}</figcaption>
      {!steps.length || steps[0].count === 0 ? <p className="muted">No requests in this period.</p> : (
        <ol>
          {steps.map((s, i) => {
            const prev = i ? steps[i - 1].count : null;
            const conv = prev ? `${Math.round((s.count / prev) * 100)}% of previous step` : "";
            return (
              <li key={s.step} title={`${s.step}: ${s.count}${conv ? ` (${conv})` : ""}`}><span className="k">{s.step}</span>
                <span className="track" aria-hidden="true"><span className="fill" style={{ width: `${(s.count / top) * 100}%` }} /></span>
                <span className="v">{s.count}</span>{i > 0 && <span className="muted tiny conv">{prev ? `${Math.round((s.count / prev) * 100)}%` : "—"}</span>}</li>);
          })}
        </ol>)}
    </figure>
  );
}

/** Turns-per-day column chart as inline SVG with a text alternative. */
export function DailyChart({ series, title = "Chat turns per day" }: { series: { date: string; turns: number }[]; title?: string }) {
  const max = Math.max(1, ...series.map((s) => s.turns));
  const w = 28, h = 90;
  return (
    <figure className="card">
      <figcaption>{title}</figcaption>
      <svg role="img" aria-label={`${title}: ${series.map((s) => `${s.date} ${s.turns}`).join(", ")}`} viewBox={`0 0 ${series.length * w} ${h + 16}`} className="daily">
        {series.map((s, i) => {
          const bh = (s.turns / max) * h;
          return <g key={s.date}><rect x={i * w + 4} y={h - bh} width={w - 8} height={bh} rx="2" className="col"><title>{`${s.date}: ${s.turns}`}</title></rect>
            {(i % Math.ceil(series.length / 7) === 0) && <text x={i * w + w / 2} y={h + 12} textAnchor="middle" className="axis">{s.date.slice(5)}</text>}</g>;
        })}
      </svg>
    </figure>
  );
}
