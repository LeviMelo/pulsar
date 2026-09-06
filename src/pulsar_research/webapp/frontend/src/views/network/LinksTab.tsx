/* The one tab only this screen can make.
 *
 * Everything else on a supervisor's record is available from a ranking. The
 * neighbourhood is not: it exists only once the ties are drawn, and it is the
 * reason the graph is worth opening at all. So it is added to the shared record
 * rather than forking it.
 */

import type { GraphNode, NetworkPayload, ProfessorDetail } from '../../api/types';
import { Neighbour, Neighbours } from '../../components/Neighbours';
import { STATE_LABEL } from '../../components/records';
import { Chip, Empty, FieldBlock } from '../../components/ui';
import { num, short, sortRows } from '../../lib/format';
import type { Adjacency } from './lenses';

export function LinksTab({ node, record, data, adjacency, byId, onSelect }: {
  node: GraphNode;
  record: ProfessorDetail;
  data: NetworkPayload;
  adjacency: Adjacency;
  byId: Map<string, GraphNode>;
  onSelect: (id: string) => void;
}) {
  const because = new Map<string, string | undefined>();
  for (const edge of data.edges) {
    if (edge.source === node.id) because.set(edge.target, edge.because);
    else if (edge.target === node.id) because.set(edge.source, edge.because);
  }

  const here = [...(adjacency.get(node.id) || new Map()).entries()]
    .map(([id, weight]) => ({ ...(byId.get(id) as GraphNode), weight }))
    .filter((n) => n.id);
  const ranked = sortRows(here, 'weight', 'desc');
  const untouched = ranked.filter((n) => !n.contacted);
  const outside = sortRows(
    (record.collaborators || []).filter((c) => !c.target_siape), 'weight', 'desc',
  );

  const mode = data.modes?.find((m) => m.key === data.mode);
  const modeLabel = (mode ? mode.label : data.mode).toLowerCase();

  return (
    <div>
      <FieldBlock title={`Neighbours under "${modeLabel}"`}>
        {ranked.length
          ? (
            <Neighbours>
              {ranked.map((n) => (
                <Neighbour key={n.id} title={short(n.name, 34)}
                           // Why this tie exists, not just that it does.
                           hint={short(because.get(n.id) || n.center, 46)}
                           tail={(
                             <>
                               {Number.isFinite(n.current_pct as number) && (
                                 <span className="num">p{num(n.current_pct, 0)}</span>
                               )}
                               {n.funded_slots ? (
                                 <Chip tone="good">{num(n.funded_slots)} slot</Chip>
                               ) : null}
                               {n.contacted && (
                                 <Chip tone="accent">
                                   {n.state ? STATE_LABEL[n.state] : 'written'}
                                 </Chip>
                               )}
                             </>
                           )}
                           onClick={() => onSelect(n.id)} />
              ))}
            </Neighbours>
          )
          : (
            <Empty>
              Isolated under this edge meaning. Try another one before concluding
              anything — co-authorship and shared technique disagree often.
            </Empty>
          )}
      </FieldBlock>

      {untouched.length > 0 && (
        <p className="panel-note">
          {num(untouched.length)} of these have not been written to. If this thread
          becomes a conversation, they are the people it can plausibly reach.
        </p>
      )}

      {outside.length > 0 && (
        <FieldBlock style={{ marginTop: '16px' }}
                    title={`Co-authors outside this faculty (${num(outside.length)})`}
                    note={'Drawn nowhere in the graph on purpose: thousands of degree-one '
                        + 'leaves would bury the structure among the indexed supervisors.'}>
          <div className="chips">
            {outside.slice(0, 40).map((c) => (
              <Chip key={c.collaborator_name} title={`weight ${num(c.weight, 2)}`}>
                {short(c.collaborator_name, 30)}
              </Chip>
            ))}
          </div>
        </FieldBlock>
      )}
    </div>
  );
}
