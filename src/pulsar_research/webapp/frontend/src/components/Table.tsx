/* The sortable table.
 *
 * Sorting is stable and always puts absent values last regardless of direction,
 * because a missing percentile is missing rather than the smallest one — a
 * table that answers "worst fit first" with the unprofiled rows is answering a
 * different question than the one asked.
 */

import type { ReactNode } from 'react';
import { text, type SortDir } from '../lib/format';
import { Empty } from './ui';

export interface Column<T> {
  key: string;
  label: ReactNode;
  align?: 'right';
  width?: string;
  truncate?: boolean;
  sortable?: boolean;
  title?: string;
  render?: (row: T) => ReactNode;
  tooltip?: (row: T) => string;
}

export function Table<T>({
  rows, columns, rowKey, selected, sortKey, sortDir = 'desc', maxHeight,
  onSort, onSelect, emptyMessage = 'Nothing matches these filters.',
}: {
  rows: readonly T[];
  columns: readonly Column<T>[];
  rowKey: (row: T) => string;
  selected?: string;
  sortKey?: string;
  sortDir?: SortDir;
  maxHeight?: string;
  onSort?: (key: string) => void;
  onSelect?: (row: T) => void;
  emptyMessage?: string;
}) {
  if (!rows.length) return <div className="table-wrap"><Empty>{emptyMessage}</Empty></div>;

  const style = maxHeight
    ? ({ '--table-h': maxHeight } as React.CSSProperties)
    : undefined;

  return (
    <div className="table-wrap" style={style}>
      <table>
        <thead>
          <tr>
            {columns.map((col) => {
              const sortable = col.sortable !== false && Boolean(onSort);
              return (
                <th key={col.key}
                    className={[col.align === 'right' && 'num', !sortable && 'no-sort']
                      .filter(Boolean).join(' ')}
                    style={col.width ? { width: col.width } : undefined}
                    title={col.title}
                    onClick={sortable ? () => onSort!(col.key) : undefined}>
                  {col.label}
                  {col.key === sortKey && (
                    <span className="arrow">{sortDir === 'desc' ? '▼' : '▲'}</span>
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const key = rowKey(row);
            return (
              <tr key={key}
                  aria-selected={selected !== undefined && key === selected ? true : undefined}
                  onClick={onSelect ? () => onSelect(row) : undefined}>
                {columns.map((col) => (
                  <td key={col.key}
                      className={[col.align === 'right' && 'num', col.truncate && 'truncate']
                        .filter(Boolean).join(' ')}
                      title={col.tooltip ? col.tooltip(row) : undefined}>
                    {col.render
                      ? col.render(row)
                      : text((row as Record<string, unknown>)[col.key])}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Sort state as one hook, since every list screen wants exactly this. */
export function nextSort(
  current: { key: string; dir: SortDir },
  key: string,
): { key: string; dir: SortDir } {
  if (current.key === key) return { key, dir: current.dir === 'desc' ? 'asc' : 'desc' };
  return { key, dir: 'desc' };
}
