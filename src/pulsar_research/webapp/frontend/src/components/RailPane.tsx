/* The rail, wired to the shared records.
 *
 * Six of the seven screens want exactly this: a stack, a root view when nothing
 * is pushed, and the standard supervisor/plan/message records on top of it.
 * Only the graph adds anything of its own, and it does so through `extraTabs`
 * rather than by rebuilding the record.
 */

import { useMemo, type ReactNode } from 'react';
import type { ProfessorDetail } from '../api/types';
import { FrameView, type ExtraTab, type RecordContext } from './records';
import { Rail, type RailStack } from './Rail';

export function RailPane({ stack, rootLabel, root, here, extraTabs }: {
  stack: RailStack;
  rootLabel: ReactNode;
  root: ReactNode;
  here?: string;
  extraTabs?: (detail: ProfessorDetail) => readonly ExtraTab[];
}) {
  const ctx = useMemo<RecordContext>(() => ({
    open: stack.push, here,
  }), [stack.push, here]);

  const top = stack.top;

  return (
    <Rail stack={stack} rootLabel={rootLabel} root={root}>
      {top && (
        <FrameView
          // Remount on identity change so a record never shows the previous
          // one's tab while its own payload is still arriving.
          key={frameKey(top)}
          frame={top}
          ctx={ctx}
          onTab={(tab) => stack.patchTop({ tab } as never)}
          extraTabs={extraTabs} />
      )}
    </Rail>
  );
}

function frameKey(frame: RailStack['frames'][number]): string {
  if (frame.kind === 'message') return `message:${frame.campaign}:${frame.siape}`;
  return `${frame.kind}:${frame.id}`;
}
