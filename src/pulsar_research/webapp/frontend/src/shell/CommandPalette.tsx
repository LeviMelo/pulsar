/* Ctrl-K: jump to any section, work plan or supervisor by name.
 *
 * The corpus is two hundred rows, so the whole index sits in memory and the
 * filter is a substring scan — there is nothing here worth a search library.
 * It exists because the alternative to "type the name" is remembering which of
 * seven screens lists the thing you are thinking of.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useOpportunities, useProfessors, useSearchAll } from '../api/client';
import { familyLabel } from '../components/records/store';
import { short } from '../lib/format';
import { pathTo } from '../lib/params';
import { ROUTES } from './routes';

interface Item { kind: string; label: string; hint?: string; to: string }

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  // Already fetched by the screens themselves; React Query hands over the
  // cached payloads rather than asking the server twice.
  const { data: opps } = useOpportunities();
  const { data: profs } = useProfessors();
  // Records, institutions, venues and co-authors live on the server: forty
  // thousand rows are not worth holding in a palette.
  const { data: remote } = useSearchAll(query);

  const items = useMemo<Item[]>(() => [
    ...ROUTES.map((r) => ({ kind: 'section', label: r.title, to: pathTo(r.id) })),
    ...(opps?.rows || []).map((r) => ({
      kind: 'plan',
      label: r.plan_title || r.project_title || r.id_opportunity,
      hint: r.professor_name ?? undefined,
      to: pathTo('opportunities', { sel: r.id_opportunity }),
    })),
    ...(profs?.rows || []).map((r) => ({
      kind: 'professor',
      label: r.canonical_name || r.siape,
      hint: r.department ?? undefined,
      to: pathTo('professors', { sel: r.siape }),
    })),
  ], [opps, profs]);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const pool = needle
      ? items.filter((i) => `${i.label} ${i.hint || ''}`.toLowerCase().includes(needle))
      : items.filter((i) => i.kind === 'section');
    const served: Item[] = needle && remote ? [
      ...remote.entities.map((e) => ({
        kind: e.kind, label: e.name, hint: e.kind === 'person' ? 'named on records' : undefined,
        to: pathTo('explore', { entity: e.entity_id }),
      })),
      ...remote.records.map((r) => ({
        kind: familyLabel(r.family).toLowerCase(), label: short(r.title, 90),
        hint: [r.year, r.subject].filter(Boolean).join(' · '),
        to: pathTo('explore', { sel: r.record_id }),
      })),
      ...(remote.records_total && remote.records_total > remote.records.length ? [{
        kind: 'search', label: `All ${remote.records_total} records matching “${query.trim()}”`,
        to: pathTo('explore', { q: query.trim() }),
      }] : []),
    ] : [];
    return [...pool.slice(0, 24), ...served].slice(0, 60);
  }, [items, query, remote]);

  useEffect(() => { setCursor(0); }, [query]);
  useEffect(() => {
    if (open) { setQuery(''); input.current?.focus(); }
  }, [open]);

  if (!open) return null;

  const choose = (item: Item) => { onClose(); navigate(item.to); };

  return (
    <div className="palette" onClick={(e) => {
      if (e.target === e.currentTarget) onClose();
    }}>
      <div className="palette-box" role="dialog" aria-modal="true" aria-label="Jump to">
        <input ref={input} type="text" value={query} autoComplete="off" spellCheck={false}
               placeholder="A professor, a plan, a paper, a journal, an institution, a co-author…"
               onChange={(e) => setQuery(e.target.value)}
               onKeyDown={(event) => {
                 if (event.key === 'ArrowDown') {
                   event.preventDefault();
                   setCursor((c) => (matches.length ? (c + 1) % matches.length : 0));
                 } else if (event.key === 'ArrowUp') {
                   event.preventDefault();
                   setCursor((c) => (matches.length ? (c - 1 + matches.length) % matches.length : 0));
                 } else if (event.key === 'Enter') {
                   const match = matches[cursor];
                   if (match) choose(match);
                 } else if (event.key === 'Escape') {
                   onClose();
                 }
               }} />
        <ul className="palette-list">
          {matches.length
            ? matches.map((item, index) => (
              <li key={`${item.kind}-${item.to}-${index}`}
                  aria-selected={index === cursor}
                  onMouseEnter={() => setCursor(index)}
                  onClick={() => choose(item)}>
                <span className="k">{item.kind}</span>
                <span className="t">{item.label}</span>
                {item.hint && <span className="faint">{item.hint}</span>}
              </li>
            ))
            : <li><span className="t faint">Nothing matches.</span></li>}
        </ul>
        <div className="palette-hint">
          <kbd>↑</kbd><kbd>↓</kbd> move · <kbd>↵</kbd> open · <kbd>esc</kbd> close
        </div>
      </div>
    </div>
  );
}
