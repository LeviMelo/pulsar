/* The console's vocabulary of small parts.
 *
 * These are the pieces every screen is assembled from — a panel, a chip, a
 * percentile meter, a tab bar. They carry no data logic at all: the point of
 * putting them here is that "a funded-slot chip" looks the same on the map, in
 * the graph's rail and inside a campaign draft, so a supervisor cannot appear
 * to be two different people on two different screens.
 */

import type { ReactNode } from 'react';
import { num } from '../lib/format';

export type Tone = '' | 'accent' | 'good' | 'warn' | 'bad' | 'info';

const cls = (...parts: (string | false | null | undefined)[]) =>
  parts.filter(Boolean).join(' ');

export function Panel({ title, note, actions, flush, children, style }: {
  title?: ReactNode;
  note?: ReactNode;
  actions?: ReactNode;
  flush?: boolean;
  children?: ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <section className="panel" style={style}>
      {(title || note) && (
        <div className="panel-head">
          {title && <h2>{title}{actions}</h2>}
          {note && <p className="panel-note">{note}</p>}
        </div>
      )}
      {flush ? children : <div className="panel-body">{children}</div>}
    </section>
  );
}

export function Stat({ label, value, foot, tone }: {
  label: ReactNode; value: ReactNode; foot?: ReactNode; tone?: Tone;
}) {
  return (
    <div className={cls('stat', tone)}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {foot && <div className="foot">{foot}</div>}
    </div>
  );
}

export function Chip({ tone, title, onClick, children }: {
  tone?: Tone; title?: string; onClick?: () => void; children: ReactNode;
}) {
  if (onClick) {
    return (
      <button type="button" className={cls('chip', tone)} title={title}
              style={{ cursor: 'pointer' }} onClick={onClick}>
        {children}
      </button>
    );
  }
  return <span className={cls('chip', tone)} title={title}>{children}</span>;
}

/** A percentile as a bar plus its value; absent percentiles draw no bar. */
export function Meter({ value, max = 100, tone = 'var(--accent)' }: {
  value: number | null | undefined; max?: number; tone?: string;
}) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return <span className="faint">—</span>;
  }
  const ratio = Math.max(0, Math.min(1, Number(value) / max));
  return (
    <div className="meter">
      <div className="track">
        <div className="fill" style={{ width: `${ratio * 100}%`, background: tone }} />
      </div>
      <span className="val">{num(value, 0)}</span>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Loading({ children = 'Loading…' }: { children?: ReactNode }) {
  return <div className="loading">{children}</div>;
}

export function Banner({ tone = 'info', children }: { tone?: Tone; children: ReactNode }) {
  return <div className={cls('banner', tone)}>{children}</div>;
}

export function FieldBlock({ title, note, children, style }: {
  title?: ReactNode; note?: ReactNode; children?: ReactNode; style?: React.CSSProperties;
}) {
  return (
    <div className="field-block" style={style}>
      {title && <h3>{title}</h3>}
      {children}
      {note && <p className="panel-note">{note}</p>}
    </div>
  );
}

/** A labelled prose block, for scraped free text that is often simply absent. */
export function Field({ label, value }: { label: ReactNode; value?: string | null }) {
  return (
    <div className="field-block">
      <h3>{label}</h3>
      <div className={cls('prose', !value && 'muted')}>
        {value?.trim() || 'Not recorded in the source.'}
      </div>
    </div>
  );
}

export function Tabs<K extends string>({ items, active, onChange }: {
  items: readonly (readonly [K, ReactNode])[];
  active: K;
  onChange: (key: K) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {items.map(([key, label]) => (
        <button key={key} type="button" role="tab"
                aria-selected={key === active}
                onClick={() => onChange(key)}>
          {label}
        </button>
      ))}
    </div>
  );
}

/**
 * The question tabs: a row of alternatives, each of which is a whole way of
 * reading the screen rather than a column to toggle.
 */
export function Asks<K extends string>({ items, active, onPick }: {
  items: readonly { key: K; label: ReactNode; hint?: string }[];
  active: K;
  onPick: (key: K) => void;
}) {
  return (
    <div className="asks" role="tablist">
      {items.map((item) => (
        <button key={item.key} type="button" role="tab" title={item.hint}
                aria-selected={item.key === active}
                onClick={() => onPick(item.key)}>
          {item.label}
        </button>
      ))}
    </div>
  );
}

export function Select<T extends string>({ label, value, options, onChange, allLabel, style }: {
  label?: ReactNode;
  value: T | '';
  options: readonly (readonly [T, ReactNode])[];
  onChange: (value: T) => void;
  allLabel?: string;
  style?: React.CSSProperties;
}) {
  const control = (
    <select value={value} style={style}
            onChange={(e) => onChange(e.target.value as T)}>
      {allLabel !== undefined && <option value="">{allLabel}</option>}
      {options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}
    </select>
  );
  if (!label) return control;
  return <label className="field">{label}{control}</label>;
}
