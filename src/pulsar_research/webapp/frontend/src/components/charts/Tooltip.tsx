/* One tooltip for the whole console.
 *
 * Charts are dense — sixty-five circles, two hundred bars — and giving each
 * mark its own React-managed tooltip element would put a few hundred hidden
 * nodes in the tree for the sake of one visible box. Instead there is a single
 * floating element driven by a tiny external store, and a mark only publishes
 * "here is my content" on mouse move.
 */

import {
  useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode,
} from 'react';

interface TipState { x: number; y: number; content: ReactNode }

let state: TipState | null = null;
const listeners = new Set<() => void>();

function emit() { for (const listener of listeners) listener(); }

export function showTip(event: { clientX: number; clientY: number }, content: ReactNode) {
  state = { x: event.clientX, y: event.clientY, content };
  emit();
}

export function hideTip() {
  if (!state) return;
  state = null;
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Spread onto any SVG mark to give it the shared tooltip. */
export function tipHandlers(content: ReactNode) {
  return {
    onMouseMove: (event: React.MouseEvent) => showTip(event, content),
    onMouseLeave: hideTip,
  };
}

export function Tooltip() {
  const tip = useSyncExternalStore(subscribe, () => state, () => null);
  const box = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState({ left: 0, top: 0 });

  // Measured after paint so the box can be flipped when it would overflow the
  // viewport; a tooltip clipped by the window edge is worse than none.
  useLayoutEffect(() => {
    if (!tip || !box.current) return;
    const pad = 14;
    const rect = box.current.getBoundingClientRect();
    let left = tip.x + pad;
    let top = tip.y + pad;
    if (left + rect.width > window.innerWidth - 8) left = tip.x - rect.width - pad;
    if (top + rect.height > window.innerHeight - 8) top = tip.y - rect.height - pad;
    setPlace({ left: Math.max(8, left), top: Math.max(8, top) });
  }, [tip]);

  useLayoutEffect(() => {
    window.addEventListener('scroll', hideTip, true);
    return () => window.removeEventListener('scroll', hideTip, true);
  }, []);

  if (!tip) return null;
  return (
    <div ref={box} className="tip"
         style={{ display: 'block', left: place.left, top: place.top }}>
      {tip.content}
    </div>
  );
}

/** The two-line tooltip body every chart uses: a bold title over faint detail. */
export function TipBody({ title, meta }: { title: ReactNode; meta?: ReactNode }) {
  return <><b>{title}</b>{meta ? <span className="meta">{meta}</span> : null}</>;
}
