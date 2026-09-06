/* View state lives in the URL.
 *
 * Filters, the selected row, the active tab: all of it is addressable, so a
 * view is reproducible, linkable and survives a reload. That was the concrete
 * thing the original console could not do — it had no addressable state at all,
 * and "which plan was I looking at" was unanswerable after a refresh.
 *
 * Writes replace the history entry by default. Typing six characters into a
 * search box must not cost six presses of the back button; the handful of
 * changes that are genuinely navigation ask for a push explicitly.
 */

import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

export type ParamPatch = Record<string, string | number | boolean | null | undefined>;

export function useViewParams() {
  const [params, setSearchParams] = useSearchParams();

  const set = useCallback((patch: ParamPatch, { push = false } = {}) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(patch)) {
        if (value === null || value === undefined || value === '' || value === false) {
          next.delete(key);
        } else {
          next.set(key, String(value));
        }
      }
      return next;
    }, { replace: !push });
  }, [setSearchParams]);

  return useMemo(() => ({
    /** A raw parameter, or '' when absent. */
    get: (key: string) => params.get(key) ?? '',
    /** A parameter constrained to a known set, falling back to the first. */
    pick: <T extends string>(key: string, allowed: readonly T[], fallback: T): T => {
      const value = params.get(key) as T | null;
      return value && allowed.includes(value) ? value : fallback;
    },
    number: (key: string, fallback = 0) => {
      const raw = Number(params.get(key));
      return Number.isFinite(raw) && params.get(key) !== null ? raw : fallback;
    },
    flag: (key: string) => params.get(key) === '1',
    set,
    params,
  }), [params, set]);
}

/** Build a hash-router path with a query, for the links that do leave a screen. */
export function pathTo(route: string, patch: ParamPatch = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(patch)) {
    if (value !== null && value !== undefined && value !== '' && value !== false) {
      query.set(key, String(value));
    }
  }
  const search = query.toString();
  return `/${route}${search ? `?${search}` : ''}`;
}
