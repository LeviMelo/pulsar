/* Hand-drawn SVG charts.
 *
 * A charting library would be the largest dependency in the console and would
 * still need overriding to match the theme, so the four marks this application
 * actually uses are drawn directly: a scatter for the semantic map, horizontal
 * bars for skill and topic supply, grouped bars for the retrieval battery, and
 * a distribution strip for placing one value inside a population.
 *
 * Every chart reads its colours from CSS custom properties, so light and dark
 * are handled by the same code and a theme switch needs no redraw logic.
 */

import { useMemo, useState, type ReactNode } from 'react';
import { Empty } from '../ui';
import { tipHandlers, TipBody } from './Tooltip';

export const SERIES = ['--c1', '--c2', '--c3', '--c4', '--c5', '--c6', '--c7', '--c8'];

/** Stable colour per category name, so a topic keeps its hue across views. */
export function palette(keys: readonly string[]): Map<string, string> {
  const map = new Map<string, string>();
  keys.forEach((key, index) => map.set(key, `var(${SERIES[index % SERIES.length]})`));
  return map;
}

function scale(domain: [number, number], range: [number, number]) {
  const span = domain[1] - domain[0] || 1;
  return (value: number) => range[0] + ((value - domain[0]) / span) * (range[1] - range[0]);
}

function extent(values: readonly number[]): [number, number] {
  const finite = values.filter((v) => Number.isFinite(v));
  if (!finite.length) return [0, 1];
  const lo = Math.min(...finite);
  const hi = Math.max(...finite);
  return lo === hi ? [lo - 1, hi + 1] : [lo, hi];
}

function truncate(value: string, limit: number) {
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value;
}

export function Legend({ children }: { children: ReactNode }) {
  return <div className="legend">{children}</div>;
}

/* --------------------------------------------------------------- scatter */

export interface ScatterProps<T> {
  rows: readonly T[];
  x?: (row: T) => number | null | undefined;
  y?: (row: T) => number | null | undefined;
  colorBy: (row: T) => string;
  sizeBy?: (row: T) => number;
  height?: number;
  label: (row: T) => ReactNode;
  meta?: (row: T) => ReactNode;
  onPick?: (row: T) => void;
  highlight?: (row: T) => boolean;
  keyOf: (row: T) => string;
}

/**
 * The semantic map. Position is a projection, so the axes are deliberately
 * unlabelled and untickmarked: reading a distance off this plot is exactly the
 * mistake the surrounding copy warns about.
 */
export function Scatter<T>({
  rows, x = (r) => (r as never)['x'], y = (r) => (r as never)['y'],
  colorBy, sizeBy, height = 460, label, meta, onPick, highlight, keyOf,
}: ScatterProps<T>) {
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());

  const points = rows.filter((r) => Number.isFinite(x(r)) && Number.isFinite(y(r)));
  const width = 900;

  const geometry = useMemo(() => {
    if (!points.length) return null;
    const pad = 24;
    return {
      sx: scale(extent(points.map((p) => Number(x(p)))), [pad, width - pad]),
      sy: scale(extent(points.map((p) => Number(y(p)))), [height - pad, pad]),
      sizes: sizeBy ? extent(points.map((p) => sizeBy(p) ?? 0)) : null,
    };
    // The projection only changes when the point set or the frame does.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, height, Boolean(sizeBy)]);

  if (!points.length || !geometry) return <Empty>No projected points in this space.</Empty>;

  const categories = [...new Set(points.map(colorBy))].sort();
  const colours = palette(categories);
  const radius = (p: T) => (geometry.sizes
    ? 4 + 7 * (((sizeBy!(p) ?? 0) - geometry.sizes[0])
      / ((geometry.sizes[1] - geometry.sizes[0]) || 1))
    : 5.5);

  const toggle = (category: string) => setHidden((prev) => {
    const next = new Set(prev);
    if (next.has(category)) next.delete(category); else next.add(category);
    return next;
  });

  return (
    <div>
      <svg className="chart" viewBox={`0 0 ${width} ${height}`} height={height}
           preserveAspectRatio="xMidYMid meet" role="img"
           aria-label="Semantic map of projects">
        {points.map((point) => {
          const category = colorBy(point);
          if (hidden.has(category)) return null;
          const lit = highlight?.(point);
          return (
            <circle key={keyOf(point)} className="mark"
                    cx={geometry.sx(Number(x(point)))}
                    cy={geometry.sy(Number(y(point)))}
                    r={radius(point)}
                    fill={colours.get(category)}
                    fillOpacity={lit ? 1 : 0.78}
                    stroke={lit ? 'var(--text)' : 'var(--surface)'}
                    strokeWidth={lit ? 2 : 0.8}
                    style={onPick ? { cursor: 'pointer' } : undefined}
                    onClick={onPick ? () => onPick(point) : undefined}
                    {...tipHandlers(<TipBody title={label(point)} meta={meta?.(point)} />)} />
          );
        })}
      </svg>
      <Legend>
        {categories.map((category) => (
          <span key={category} className={hidden.has(category) ? 'off' : undefined}
                onClick={() => toggle(category)}>
            <i style={{ background: colours.get(category) }} />
            {category}
          </span>
        ))}
      </Legend>
    </div>
  );
}

/* ------------------------------------------------------------ horizontal */

export interface BarRow { label: string; value: number; category?: string }

export function BarsH({
  rows, rowHeight = 22, max, format = (v) => String(v), onPick, meta, colorByCategory,
}: {
  rows: readonly BarRow[];
  rowHeight?: number;
  max?: number | null;
  format?: (value: number) => string;
  onPick?: (row: BarRow) => void;
  meta?: (row: BarRow) => ReactNode;
  colorByCategory?: boolean;
}) {
  if (!rows.length) return <Empty>Nothing to plot.</Empty>;
  const width = 720;
  const labelWidth = 260;
  const barWidth = width - labelWidth - 56;
  const top = max ?? Math.max(...rows.map((r) => r.value ?? 0), 1);
  const categories = colorByCategory
    ? [...new Set(rows.map((r) => r.category ?? '—'))].sort()
    : [];
  const colours = palette(categories);
  const totalHeight = rows.length * rowHeight + 6;

  return (
    <div>
      <svg className="chart" viewBox={`0 0 ${width} ${totalHeight}`} height={totalHeight}
           preserveAspectRatio="xMinYMin meet">
        {rows.map((row, index) => {
          const y = index * rowHeight + 3;
          const w = Math.max(1, ((row.value ?? 0) / top) * barWidth);
          return (
            <g key={`${row.label}-${index}`}>
              <text className="label" x={labelWidth - 9} y={y + rowHeight / 2 - 1}
                    textAnchor="end" dominantBaseline="middle">
                {truncate(String(row.label ?? '—'), 42)}
              </text>
              <rect className="mark" x={labelWidth} y={y} width={w} height={rowHeight - 8} rx={3}
                    fill={colorByCategory ? colours.get(row.category ?? '—') : 'var(--accent)'}
                    fillOpacity={0.85}
                    style={onPick ? { cursor: 'pointer' } : undefined}
                    onClick={onPick ? () => onPick(row) : undefined}
                    {...(meta
                      ? tipHandlers(<TipBody title={row.label} meta={meta(row)} />)
                      : {})} />
              <text className="tick" x={labelWidth + w + 7} y={y + rowHeight / 2 - 1}
                    dominantBaseline="middle">
                {format(row.value)}
              </text>
            </g>
          );
        })}
      </svg>
      {categories.length > 1 && (
        <Legend>
          {categories.map((c) => (
            <span key={c}><i style={{ background: colours.get(c) }} />{c}</span>
          ))}
        </Legend>
      )}
    </div>
  );
}

/* --------------------------------------------------------- grouped bars */

export interface GroupedRow { group: string; series: string; value: number; note?: ReactNode }

/** The retrieval battery: one group per task, one bar per channel. */
export function GroupedBars({
  rows, format = (v) => v.toFixed(3), rowHeight = 15, groupGap = 14, emphasise = 'fused',
}: {
  rows: readonly GroupedRow[];
  format?: (value: number) => string;
  rowHeight?: number;
  groupGap?: number;
  emphasise?: string;
}) {
  if (!rows.length) return <Empty>No benchmark rows.</Empty>;
  const groups = [...new Set(rows.map((r) => r.group))];
  const seriesKeys = [...new Set(rows.map((r) => r.series))];
  const colours = palette(seriesKeys);
  const width = 860;
  const labelWidth = 210;
  const barWidth = width - labelWidth - 60;
  const top = Math.max(...rows.map((r) => r.value ?? 0), 0.0001);
  const groupHeight = seriesKeys.length * rowHeight + groupGap;
  const totalHeight = groups.length * groupHeight + 6;

  return (
    <div>
      <svg className="chart" viewBox={`0 0 ${width} ${totalHeight}`} height={totalHeight}
           preserveAspectRatio="xMinYMin meet">
        {groups.map((name, gi) => {
          const y0 = gi * groupHeight + 2;
          return (
            <g key={name}>
              <text className="label" x={labelWidth - 9}
                    y={y0 + (seriesKeys.length * rowHeight) / 2}
                    textAnchor="end" dominantBaseline="middle">
                {truncate(String(name).replace(/_/g, ' '), 30)}
              </text>
              {seriesKeys.map((key, si) => {
                const row = rows.find((r) => r.group === name && r.series === key);
                if (!row) return null;
                const w = Math.max(1, ((row.value ?? 0) / top) * barWidth);
                return (
                  <g key={key}>
                    <rect className="mark" x={labelWidth} y={y0 + si * rowHeight + 1}
                          width={w} height={rowHeight - 3} rx={2}
                          fill={colours.get(key)}
                          fillOpacity={key === emphasise ? 1 : 0.68}
                          {...tipHandlers(
                            <TipBody title={`${name} · ${key}`}
                                     meta={<>{format(row.value)}{row.note ? <><br />{row.note}</> : null}</>} />,
                          )} />
                    <text className="tick" x={labelWidth + w + 6}
                          y={y0 + si * rowHeight + rowHeight / 2} dominantBaseline="middle">
                      {format(row.value)}
                    </text>
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      <Legend>
        {seriesKeys.map((key) => (
          <span key={key}>
            <i style={{ background: colours.get(key) }} />{key.replace(/_/g, ' ')}
          </span>
        ))}
      </Legend>
    </div>
  );
}

/* -------------------------------------------------------- distribution */

/** A histogram strip, for showing where one value sits in a whole population. */
export function Distribution({
  values, marker, bins = 24, height = 62, label,
}: {
  values: readonly (number | null | undefined)[];
  marker?: number | null;
  bins?: number;
  height?: number;
  label?: ReactNode;
}) {
  const finite = values.filter((v): v is number => Number.isFinite(v as number));
  if (!finite.length) return <Empty>No distribution.</Empty>;
  const width = 420;
  const counts = new Array(bins).fill(0) as number[];
  for (const v of finite) {
    counts[Math.min(bins - 1, Math.max(0, Math.floor((v / 100) * bins)))] += 1;
  }
  const top = Math.max(...counts, 1);
  const bw = width / bins;
  const pin = Number.isFinite(marker as number) ? (marker as number) : null;

  return (
    <div>
      <svg className="chart" viewBox={`0 0 ${width} ${height}`} height={height}
           preserveAspectRatio="xMidYMid meet">
        {counts.map((count, index) => (
          <rect key={index} x={index * bw + 0.6} width={bw - 1.2}
                y={height - 14 - (count / top) * (height - 20)}
                height={(count / top) * (height - 20)} rx={1.5}
                fill="var(--surface-3)" />
        ))}
        {pin !== null && (
          <>
            <line x1={(pin / 100) * width} x2={(pin / 100) * width} y1={2} y2={height - 12}
                  stroke="var(--accent)" strokeWidth={2} />
            <text className="tick" x={(pin / 100) * width} y={height - 2}
                  textAnchor="middle" fill="var(--accent)">
              p{Math.round(pin)}
            </text>
          </>
        )}
      </svg>
      {label && <Legend><span>{label}</span></Legend>}
    </div>
  );
}
