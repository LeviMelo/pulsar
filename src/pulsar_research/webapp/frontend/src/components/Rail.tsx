/* The rail: a stack of records that opens over whatever you are looking at.
 *
 * Every screen in this console has the same shape of question — a field of
 * things, and one of them you want to read. The mistake this module exists to
 * prevent is answering the second question by navigating: clicking a professor
 * on the map, or the plan behind a campaign draft, used to route to another
 * screen and throw away the filters, the scroll position, the map's camera and
 * the layout the graph had spent three seconds converging into. Curiosity was
 * punished with a full reset.
 *
 * So going deeper happens here instead. Each record pushes onto a stack with
 * one way back, the page underneath is untouched, and the handful of links that
 * genuinely do leave for another screen are drawn differently and say so.
 */

import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { num } from '../lib/format';

export type ProfessorTab = 'profile' | 'plans' | 'skills' | 'evidence' | string;

export type Frame =
  | { kind: 'professor'; id: string; label?: string; tab?: ProfessorTab;
      summary?: Record<string, unknown> }
  | { kind: 'plan'; id: string; label?: string }
  | { kind: 'message'; campaign: string; siape: string; label?: string };

export interface RailStack {
  frames: readonly Frame[];
  push: (frame: Frame) => void;
  pop: () => void;
  reset: () => void;
  replace: (frame: Frame) => void;
  /** Patch the frame on top — how a record remembers which tab is open. */
  patchTop: (patch: Partial<Frame>) => void;
  depth: number;
  top: Frame | null;
}

export function useRail(): RailStack {
  const [frames, setFrames] = useState<Frame[]>([]);

  const push = useCallback((frame: Frame) => setFrames((f) => [...f, frame]), []);
  const pop = useCallback(() => setFrames((f) => f.slice(0, -1)), []);
  const reset = useCallback(() => setFrames([]), []);
  const replace = useCallback((frame: Frame) => setFrames([frame]), []);
  const patchTop = useCallback((patch: Partial<Frame>) => setFrames((f) => (
    f.length ? [...f.slice(0, -1), { ...f[f.length - 1], ...patch } as Frame] : f
  )), []);

  return useMemo(() => ({
    frames, push, pop, reset, replace, patchTop,
    depth: frames.length,
    top: frames[frames.length - 1] ?? null,
  }), [frames, push, pop, reset, replace, patchTop]);
}

/**
 * The rail's chrome: one way back, and the record underneath it.
 *
 * At depth zero it renders the screen's own root — which is never an
 * instruction to click something, but the answer the screen already has.
 */
export function Rail({ stack, rootLabel, root, children }: {
  stack: RailStack;
  rootLabel: ReactNode;
  root: ReactNode;
  children: ReactNode;
}) {
  if (!stack.depth) return <div className="panel detail">{root}</div>;
  const parent = stack.frames[stack.frames.length - 2];
  return (
    <div className="panel detail">
      <button type="button" className="railback" onClick={stack.pop}>
        ←<span>{parent ? (parent.label || 'Back') : rootLabel}</span>
      </button>
      {children}
    </div>
  );
}

/**
 * The findings strip: a page's conclusions, which are also its filters.
 *
 * A number nobody can act on is decoration. Every count rendered here filters
 * the view to the things it counted, so "17 funded plans nobody has answered"
 * is one click from being seventeen names.
 */
export function Findings<K extends string>({ items, active, onPick }: {
  items: readonly { key: K; label: ReactNode; count: number; hint?: string }[];
  active: K | '';
  onPick: (key: K | '') => void;
}) {
  return (
    <div className="findings">
      {items.map((item) => (
        <button key={item.key} type="button"
                className={`finding${item.key === active ? ' on' : ''}`}
                title={item.hint}
                onClick={() => onPick(item.key === active ? '' : item.key)}>
          <b>{num(item.count)}</b>{item.label}
        </button>
      ))}
    </div>
  );
}

/** A link that genuinely leaves this screen, drawn so that it reads as leaving. */
export function LeaveLink({ label, to, hint }: { label: string; to: string; hint?: string }) {
  const navigate = useNavigate();
  return (
    <button type="button" className="chip leave"
            title={hint || `Opens the ${label} and leaves this screen`}
            onClick={() => navigate(to)}>
      {label} ↗
    </button>
  );
}
