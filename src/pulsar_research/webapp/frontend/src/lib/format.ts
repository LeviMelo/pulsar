/* Formatting and sorting. No DOM, no React — just the rules the whole console
 * has to agree on about how a missing value looks and where it sorts.
 */

/** A number that may legitimately be absent renders as an em dash, never 0. */
export function num(value: unknown, digits = 0): string {
  if (value === null || value === undefined || value === '' || Number.isNaN(Number(value))) {
    return '—';
  }
  return Number(value).toLocaleString('en', {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

/**
 * Sum a column with coercion.
 *
 * `rows.reduce((a, r) => a + r.slots, 0)` is correct right up until one value
 * arrives as a string, at which point JavaScript concatenates instead of adding
 * and the total silently becomes a 157-digit number.
 */
export function total<T>(rows: readonly T[], key: keyof T | ((row: T) => unknown)): number {
  return rows.reduce((sum, row) => {
    const value = typeof key === 'function' ? key(row) : row[key];
    const parsed = Number(value);
    return sum + (Number.isFinite(parsed) ? parsed : 0);
  }, 0);
}

export function text(value: unknown, fallback = '—'): string {
  const out = (value ?? '').toString().trim();
  return out || fallback;
}

/** Collapse a scraped title for a single table line without losing its start. */
export function short(value: unknown, limit = 90): string {
  const clean = text(value, '').replace(/\s+/g, ' ').trim();
  if (!clean) return '—';
  return clean.length > limit ? `${clean.slice(0, limit - 1).trimEnd()}…` : clean;
}

export function date(value: unknown): string {
  if (!value) return '—';
  const parsed = new Date(value as string);
  if (Number.isNaN(+parsed)) return String(value);
  return parsed.toLocaleString('en', { year: 'numeric', month: 'short', day: 'numeric' });
}

export function ago(value: unknown): string {
  if (!value) return '';
  const seconds = (Date.now() - +new Date(value as string)) / 1000;
  if (Number.isNaN(seconds)) return '';
  const steps: [number, string][] = [[60, 's'], [60, 'm'], [24, 'h'], [7, 'd'], [4.35, 'w'], [12, 'mo']];
  let n = seconds;
  let unit = 's';
  for (const [size, labelUnit] of steps) {
    if (n < size) break;
    n /= size;
    unit = labelUnit;
  }
  return `${Math.max(1, Math.round(n))}${unit} ago`;
}

/** SIGAA writes departments with padded columns; nobody needs to see that. */
export function tidy(value: unknown): string {
  return String(value || '').split(/\s+/).join(' ').trim();
}

export function shortName(name: unknown): string {
  const parts = String(name || '').split(/\s+/).filter(Boolean);
  if (parts.length < 2) return parts[0] || '';
  return `${parts[0]} ${parts[parts.length - 1]}`;
}

/* ---------------------------------------------------------------- sorting */

export type SortDir = 'asc' | 'desc';

/** Keeps absent values at the bottom in both directions: a missing percentile
 *  is missing, not the smallest one. */
export function compare(a: unknown, b: unknown, dir: SortDir = 'desc'): number {
  const gone = (v: unknown) => v === null || v === undefined || v === ''
    || (typeof v === 'number' && Number.isNaN(v));
  if (gone(a) && gone(b)) return 0;
  if (gone(a)) return 1;
  if (gone(b)) return -1;
  const sign = dir === 'desc' ? -1 : 1;
  if (typeof a === 'number' && typeof b === 'number') return sign * (a - b);
  return sign * String(a).localeCompare(String(b), 'pt', { numeric: true });
}

export function sortRows<T>(
  rows: readonly T[],
  key: keyof T | string,
  dir: SortDir = 'desc',
  accessor?: (row: T) => unknown,
): T[] {
  const get = accessor || ((row: T) => (row as Record<string, unknown>)[key as string]);
  return [...rows].sort((x, y) => compare(get(x), get(y), dir));
}

export function escapeHtml(value: unknown): string {
  return String(value ?? '').replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));
}
