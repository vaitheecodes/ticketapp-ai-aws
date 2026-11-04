import { useEffect, useRef, useState } from "react";

/* A small grouped bar chart in plain SVG (no chart library): one group per
 * point, one bar per series. Drawn at the container's real width (measured)
 * so text and bars are never stretched. Colors come from --chart-1..
 * tokens so both themes work. The numbers are also in a visually hidden
 * table for screen readers. */

const HEIGHT = 180;
const PAD = { top: 12, right: 8, bottom: 28, left: 32 };

function niceMax(value) {
  if (value <= 4) return Math.max(value, 1);
  const step = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / step) * step;
}

function useWidth(ref, fallback) {
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    if (!ref.current || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, Math.floor(entry.contentRect.width))));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

export default function BarChart({ title, points, series, labelFor }) {
  const boxRef = useRef(null);
  const width = useWidth(boxRef, 640);
  const max = niceMax(Math.max(0, ...points.flatMap((p) => series.map((s) => p[s.key] || 0))));
  const plotW = width - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const groupW = plotW / Math.max(points.length, 1);
  const barW = Math.max(2, Math.min(14, (groupW * 0.65) / series.length));
  const y = (v) => PAD.top + plotH - (v / max) * plotH;
  // Whole numbers only: small counts get one gridline per unit.
  const ticks = max <= 4 ? Array.from({ length: max + 1 }, (_, i) => i) : [0, max / 2, max];
  const labelEvery = Math.ceil(points.length / Math.max(2, Math.floor(plotW / 64)));

  return (
    <figure className="chart" ref={boxRef}>
      <figcaption className="chart-title">{title}</figcaption>
      <div className="chart-legend" aria-hidden="true">
        {series.map((s, i) => (
          <span key={s.key} className="chart-legend-item">
            <span className="chart-swatch" style={{ background: `var(--chart-${i + 1})` }} />
            {s.label}
          </span>
        ))}
      </div>
      <svg viewBox={`0 0 ${width} ${HEIGHT}`} width={width} height={HEIGHT} className="chart-svg" role="img"
           aria-label={title}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} className="chart-grid" />
            <text x={PAD.left - 6} y={y(t) + 4} className="chart-axis" textAnchor="end">
              {Math.round(t)}
            </text>
          </g>
        ))}
        {points.map((p, i) => (
          <g key={i} transform={`translate(${PAD.left + i * groupW + (groupW - barW * series.length) / 2},0)`}>
            {series.map((s, j) => {
              const v = p[s.key] || 0;
              return (
                <rect key={s.key} x={j * barW} y={y(v)} width={barW - 1} height={Math.max(0, PAD.top + plotH - y(v))}
                      rx="1.5" fill={`var(--chart-${j + 1})`}>
                  <title>{`${labelFor(p)}: ${v} ${s.label.toLowerCase()}`}</title>
                </rect>
              );
            })}
            {i % labelEvery === 0 && (
              <text x={(barW * series.length) / 2} y={HEIGHT - 8} className="chart-axis" textAnchor="middle">
                {labelFor(p)}
              </text>
            )}
          </g>
        ))}
      </svg>
      <table className="sr-only">
        <caption>{title}</caption>
        <thead>
          <tr>
            <th>Date</th>
            {series.map((s) => (
              <th key={s.key}>{s.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {points.map((p, i) => (
            <tr key={i}>
              <td>{labelFor(p)}</td>
              {series.map((s) => (
                <td key={s.key}>{p[s.key] || 0}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
