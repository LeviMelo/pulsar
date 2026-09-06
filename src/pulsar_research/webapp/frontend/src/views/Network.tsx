/* The academic network — the structural view of the faculty.
 *
 * Every other screen in this console ranks: this plan fits better than that one,
 * this supervisor is in the top decile. Ranking answers "who", which is the easy
 * half of planning a campaign. The half a table cannot show is *shape*: whether
 * a strong match sits alone or inside a group three of whose members have
 * already been written to; whether the technique being matched lives in one unit
 * or crosses both; who is adjacent to a thread that has gone quiet. Those are
 * the questions that decide a second wave.
 *
 * Four things carry that here.
 *
 * 1. Edge meaning is switchable, because the three graphs disagree in useful
 *    ways: co-authorship is who has actually worked together, shared technique
 *    is who could, shared subject is who studies the same thing.
 * 2. The institutional partition is drawn, not merely coloured. ICBS and FAMED
 *    are separate faculties, and "does this technique cross the two" is a
 *    different question from "who is central". Grouping applies a real force,
 *    so the drawing shows whether the edges hold the groups together or pull
 *    them apart.
 * 3. The layout is a live simulation the operator can push around, not a
 *    picture that computes once and sets. See `simulation.ts`.
 * 4. Selecting a supervisor opens their record beside the graph and cards every
 *    neighbour in place, so the neighbourhood can be read off the drawing
 *    without clicking through it one at a time.
 *
 * Switching question re-fetches the ties and hands them to the running
 * simulation; it does not re-enter the view. Re-routing would throw away the
 * layout, the camera, every pinned node and the record being read, then spend
 * three seconds converging into a different arrangement of the same people —
 * punishing curiosity with a full reset. The people are identical between these
 * graphs; only what joins them changes.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useNetwork } from '../api/client';
import type { GraphNode, NetworkPayload } from '../api/types';
import { Neighbour, Neighbours } from '../components/Neighbours';
import { Findings, useRail } from '../components/Rail';
import { RailPane } from '../components/RailPane';
import { Asks, Banner, Chip, Empty, Loading, Select } from '../components/ui';
import { num, short, shortName, sortRows } from '../lib/format';
import { useViewParams } from '../lib/params';
import { HeaderActions } from '../shell/Shell';
import { Finder } from './network/Finder';
import { GraphCanvas, GraphKnobs } from './network/GraphCanvas';
import {
  ASKS, COLOUR_BY, EDGE_BY, GROUP_BY, SIZE_BY, facetOf,
} from './network/encodings';
import {
  buildAdjacency, buildLenses, matching, secondWave, within, type Lens,
} from './network/lenses';
import { LinksTab } from './network/LinksTab';
import { Simulation } from './network/simulation';

export function Network() {
  const params = useViewParams();
  const rail = useRail();

  const ask = ASKS.find((a) => a.key === params.get('ask')) || ASKS[0];
  const mode = params.get('mode') || ask.mode;
  const { data, error, isPending } = useNetwork(mode);

  const view = {
    ask: params.get('ask') || ask.key,
    mode,
    group: params.get('group') || 'center',
    colour: params.get('colour') || ask.colour,
    size: params.get('size') || ask.size,
    edge: params.get('edge') || ask.edge,
    lens: params.get('lens'),
    hops: params.number('hops', 1),
    names: params.flag('names'),
    q: params.get('q'),
    sel: params.get('sel'),
  };

  if (isPending) return <Loading />;
  if (error || !data) {
    return <Banner tone="bad"><div>{String((error as Error)?.message || error)}</div></Banner>;
  }
  return <Graph data={data} view={view} params={params} rail={rail} />;
}

/** Everything the drawing reads out of the URL. */
interface View {
  ask: string; mode: string; group: string; colour: string; size: string; edge: string;
  lens: string; hops: number; names: boolean; q: string; sel: string;
}

function Graph({ data, view, params, rail }: {
  data: NetworkPayload;
  view: View;
  params: ReturnType<typeof useViewParams>;
  rail: ReturnType<typeof useRail>;
}) {
  const [panelOpen, setPanelOpen] = useState(() => {
    try { return Boolean(localStorage.getItem('pulsar-network-panel')); } catch { return false; }
  });

  /* The simulation is created once and then lives across every question. Its
   * positions, pins and camera belong to the operator, not to the payload. */
  const simRef = useRef<Simulation | null>(null);
  if (!simRef.current) {
    simRef.current = new Simulation(data.nodes, data.edges, view.group, facetOf);
  }
  const sim = simRef.current;

  const byId = useMemo(() => new Map(sim.nodes.map((n) => [n.id, n as GraphNode])), [sim]);

  // Degree, and anything else the server recomputes per graph, belongs to the
  // new answer; position, velocity and pinning belong to the operator.
  const adopted = useRef<NetworkPayload | null>(null);
  if (adopted.current !== data) {
    adopted.current = data;
    for (const fresh of data.nodes) {
      const node = byId.get(fresh.id);
      if (!node) continue;
      for (const [field, value] of Object.entries(fresh)) {
        if (field !== 'x' && field !== 'y') {
          (node as unknown as Record<string, unknown>)[field] = value;
        }
      }
    }
  }

  useEffect(() => {
    sim.setEdges(data.edges);
    // Let it settle into the new ties from where it stands rather than from
    // scratch: which supervisors move and which hold still is information, and
    // it is destroyed by starting over.
    sim.reheat(0.55);
  }, [sim, data]);

  useEffect(() => { sim.regroup(view.group); }, [sim, view.group]);

  useEffect(() => () => sim.dispose(), [sim]);

  const adjacency = useMemo(() => buildAdjacency(data.edges), [data.edges]);
  const reach = useMemo(() => secondWave(sim.nodes, adjacency, byId), [sim, adjacency, byId]);
  const lenses = useMemo(
    () => buildLenses(sim.nodes, data.edges, byId, reach),
    [sim, data.edges, byId, reach],
  );
  const lens = lenses.find((l) => l.key === view.lens) || lenses[0];

  const focus = useMemo(() => {
    if (view.sel) return within(adjacency, view.sel, view.hops);
    return lenses.find((l) => l.key === view.lens)?.ids ?? null;
  }, [view.sel, view.hops, view.lens, adjacency, lenses]);

  const hits = useMemo(() => matching(sim.nodes, view.q), [sim, view.q]);

  /* ------------------------------------------------ changing the question */

  const select = (id: string) => {
    const next = id === view.sel ? '' : id;
    params.set({ sel: next });
    const node = byId.get(next);
    if (node) {
      rail.replace({
        kind: 'professor', id: next, summary: node as unknown as Record<string, unknown>,
        label: shortName(node.name), tab: params.get('tab') || 'profile',
      });
    } else {
      rail.reset();
    }
    sim.requestFit(next ? within(adjacency, next, view.hops) : (lens?.ids ?? null));
  };

  const setLens = (key: string) => {
    rail.reset();
    // Deliberately no camera move. A lens answers "who, and where in the field"
    // — flying to a set of three would show three dots and throw away the half
    // of the answer that is their position among the rest.
    params.set({ lens: key, sel: '' });
  };

  const askFor = (key: string) => {
    const option = ASKS.find((a) => a.key === key);
    if (!option) return;
    params.set({
      ask: option.key, mode: '', colour: '', size: '', edge: '', lens: '',
    });
  };

  return (
    <div>
      <HeaderActions>
        {(view.sel || view.lens) && (
          // Clearing is not navigation either: it puts the rail back to the
          // shortlist and leaves every node exactly where it is.
          <button type="button" className="btn subtle"
                  onClick={() => { rail.reset(); params.set({ sel: '', lens: '' }); }}>
            Clear
          </button>
        )}
      </HeaderActions>

      {/* Two tiers, because these controls are not asked at the same rate.
          Which question you are asking, and who you are looking for, change
          constantly. How the answer is coloured and sized is set once. */}
      <div className="toolbar">
        <Asks items={ASKS.map((a) => ({ key: a.key, label: a.label, hint: a.hint }))}
              active={view.ask} onPick={askFor} />
        <Finder nodes={sim.nodes} value={view.q}
                onType={(q) => params.set({ q })}
                onPick={(node) => {
                  params.set({ q: '' });
                  if (view.sel === node.id) {
                    sim.requestFit(within(adjacency, node.id, view.hops));
                  } else {
                    select(node.id);
                  }
                }} />
        <button type="button" className="btn subtle" aria-expanded={panelOpen}
                title="Take the encodings apart by hand"
                onClick={() => {
                  const next = !panelOpen;
                  setPanelOpen(next);
                  try {
                    localStorage.setItem('pulsar-network-panel', next ? '1' : '');
                  } catch { /* private mode */ }
                }}>
          Display
        </button>
      </div>

      {panelOpen && (
        <div className="toolbar second">
          <Select label="Edge meaning" value={view.mode}
                  options={(data.modes || []).map((m) => [m.key, m.label] as const)}
                  onChange={(value) => params.set({ mode: value, ask: '' })} />
          <Select label="Group by" value={view.group} options={GROUP_BY}
                  onChange={(value) => params.set({ group: value })} />
          <Select label="Colour" value={view.colour} options={COLOUR_BY}
                  onChange={(value) => params.set({ colour: value })} />
          <Select label="Size" value={view.size} options={SIZE_BY}
                  onChange={(value) => params.set({ size: value })} />
          <Select label="Edges" value={view.edge} options={EDGE_BY}
                  onChange={(value) => params.set({ edge: value })} />
          <label className="check">
            <input type="checkbox" checked={view.names}
                   onChange={(e) => params.set({ names: e.target.checked ? 1 : '' })} />
            All names
          </label>
          <GraphKnobs sim={sim} />
        </div>
      )}

      <div className="split wide">
        <div>
          <section className="panel">
            <div className="panel-head tight">
              <p className="panel-note">
                {num(sim.nodes.length)} supervisors · {num(data.edges.length)} ties.{' '}
                {data.note}
              </p>
              {/* The findings are the page's actual conclusions, so they are the
                  control surface too: each one filters the drawing to the
                  supervisors it counted and lists them beside it. */}
              <Findings active={view.lens} onPick={setLens}
                        items={lenses.map((l) => ({
                          key: l.key, label: l.label, count: l.ids.size, hint: l.hint,
                        }))} />
            </div>
            <GraphCanvas sim={sim} focus={focus} hits={hits} onSelect={select}
                         view={{
                           colour: view.colour, size: view.size, edge: view.edge,
                           sel: view.sel, names: view.names,
                         }} />
          </section>
        </div>

        <RailPane stack={rail} here="network" rootLabel={lens?.title ?? 'Shortlist'}
                  root={<Shortlist lens={lens} byId={byId} onSelect={select} />}
                  extraTabs={(record) => {
                    const top = rail.top;
                    const node = top && top.kind === 'professor' ? byId.get(top.id) : null;
                    if (!node) return [];
                    return [{
                      key: 'links',
                      label: `Connections (${num(node.degree)})`,
                      render: () => (
                        <LinksTab node={node} record={record} data={data}
                                      adjacency={adjacency} byId={byId} onSelect={select} />
                      ),
                    }];
                  }} />
      </div>
    </div>
  );
}

/**
 * The rail is never empty.
 *
 * It used to hold an instruction until something was clicked, which wasted half
 * the screen on telling you to do work the page could have done for you. Now it
 * opens on the answer: the people this graph says are worth writing to next,
 * ranked, each with the reason they are on the list.
 */
function Shortlist({ lens, byId, onSelect }: {
  lens: Lens | undefined;
  byId: Map<string, GraphNode>;
  onSelect: (id: string) => void;
}) {
  if (!lens) return <div className="panel-body"><Empty>Nothing to show.</Empty></div>;
  const rows = sortRows(
    [...lens.ids].map((id) => byId.get(id)).filter((n): n is GraphNode => Boolean(n)),
    'current_pct', 'desc',
  );
  return (
    <div>
      <div className="detail-head">
        <h2>{lens.title}</h2>
        <p className="sub">{lens.hint}</p>
      </div>
      <div className="panel-body">
        {rows.length
          ? (
            <Neighbours>
              {rows.map((node) => (
                <Neighbour key={node.id} title={short(node.name, 34)}
                           hint={lens.reason(node)}
                           tail={(
                             <>
                               {Number.isFinite(node.current_pct as number) && (
                                 <span className="num">p{num(node.current_pct, 0)}</span>
                               )}
                               {node.funded_slots ? (
                                 <Chip tone="good">{num(node.funded_slots)} slot</Chip>
                               ) : null}
                             </>
                           )}
                           onClick={() => onSelect(node.id)} />
              ))}
            </Neighbours>
          )
          : <Empty>{lens.blank}</Empty>}
      </div>
    </div>
  );
}
