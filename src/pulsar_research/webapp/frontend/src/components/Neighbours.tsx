/* A list of things you can open, each stating its own case.
 *
 * Used wherever the answer is "these people" or "these plans": the graph's
 * shortlist, a supervisor's open plans, the projects inside a lens. A table
 * would be wrong here — there are no columns to compare across, only a name, a
 * reason it is on the list, and one or two figures worth knowing before
 * clicking.
 */

import type { ReactNode } from 'react';

export function Neighbours({ children }: { children: ReactNode }) {
  return <div className="neighbours">{children}</div>;
}

export function Neighbour({ title, hint, tail, onClick }: {
  title: ReactNode;
  hint?: ReactNode;
  tail?: ReactNode;
  onClick: () => void;
}) {
  return (
    <button type="button" className="neighbour" onClick={onClick}>
      <span className="who">
        <b>{title}</b>
        {hint ? <span className="faint">{hint}</span> : null}
      </span>
      <span className="tail">{tail}</span>
    </button>
  );
}
