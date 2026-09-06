/* The search field, with the matches listed under it.
 *
 * Searching a graph is not searching a table: the answer is a position, not a
 * row, so picking a name has to take the camera there. Typing still highlights
 * every match in the drawing — that is how you see whether a whole department
 * matches — but choosing one from the list is what flies the camera to it.
 *
 * The list carries the unit and the funded-slot count, which is usually enough
 * to pick the right person among namesakes without opening either of them.
 */

import { useRef, useState } from 'react';
import type { GraphNode } from '../../api/types';
import { num, short } from '../../lib/format';

export function Finder({ nodes, value, onType, onPick }: {
  nodes: readonly GraphNode[];
  value: string;
  onType: (query: string) => void;
  onPick: (node: GraphNode) => void;
}) {
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const blurTimer = useRef<number | null>(null);

  const needle = value.trim().toLowerCase();
  const matches = needle
    ? nodes.filter((n) => `${n.name} ${n.department} ${n.center}`.toLowerCase()
      .includes(needle)).slice(0, 8)
    : [];

  const choose = (node: GraphNode) => {
    setOpen(false);
    onType('');
    onPick(node);
  };

  return (
    <label className="field grow finder">
      Find a supervisor
      <input type="search" value={value} placeholder="Name, department…"
             autoComplete="off" data-role="search"
             onChange={(e) => { onType(e.target.value); setCursor(0); setOpen(true); }}
             onFocus={() => setOpen(true)}
             onBlur={() => {
               // A mousedown on the list fires before blur; give it the frame.
               blurTimer.current = window.setTimeout(() => setOpen(false), 140);
             }}
             onKeyDown={(event) => {
               if (!matches.length) return;
               if (event.key === 'ArrowDown') {
                 event.preventDefault();
                 setCursor((c) => (c + 1) % matches.length);
               } else if (event.key === 'ArrowUp') {
                 event.preventDefault();
                 setCursor((c) => (c - 1 + matches.length) % matches.length);
               } else if (event.key === 'Enter') {
                 event.preventDefault();
                 choose(matches[cursor]);
               } else if (event.key === 'Escape') {
                 setOpen(false);
               }
             }} />
      <ul className="ac" hidden={!open || !matches.length}>
        {matches.map((node, index) => (
          <li key={node.id} aria-selected={index === cursor}
              onMouseEnter={() => setCursor(index)}
              onMouseDown={(event) => {
                event.preventDefault();
                if (blurTimer.current) window.clearTimeout(blurTimer.current);
                choose(node);
              }}>
            <span className="t">{short(node.name, 38)}</span>
            <span className="faint">
              {[node.center,
                node.funded_slots ? `${num(node.funded_slots)} slot` : null,
                Number.isFinite(node.current_pct as number)
                  ? `p${num(node.current_pct, 0)}` : null,
              ].filter(Boolean).join(' · ')}
            </span>
          </li>
        ))}
      </ul>
    </label>
  );
}
