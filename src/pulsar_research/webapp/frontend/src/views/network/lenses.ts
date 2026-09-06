/* Structure, and the findings drawn from it.
 *
 * Every other screen in this console ranks. Ranking answers "who", which is the
 * easy half of planning a campaign. The half a table cannot show is *shape*:
 * whether a strong match sits alone or inside a group three of whose members
 * have already been written to; whether the technique being matched lives in
 * one unit or crosses both. These functions are that half.
 */

import type { GraphEdge, GraphNode } from '../../api/types';
import { num, shortName } from '../../lib/format';

export type Adjacency = Map<string, Map<string, number>>;

export function buildAdjacency(edges: readonly GraphEdge[]): Adjacency {
  const map: Adjacency = new Map();
  for (const edge of edges) {
    if (!map.has(edge.source)) map.set(edge.source, new Map());
    if (!map.has(edge.target)) map.set(edge.target, new Map());
    map.get(edge.source)!.set(edge.target, edge.weight);
    map.get(edge.target)!.set(edge.source, edge.weight);
  }
  return map;
}

export function within(adjacency: Adjacency, root: string, hops: number): Set<string> {
  const seen = new Set([root]);
  let frontier = new Set([root]);
  for (let i = 0; i < Math.max(1, hops); i += 1) {
    const next = new Set<string>();
    for (const id of frontier) {
      for (const neighbour of (adjacency.get(id) || new Map()).keys()) {
        if (!seen.has(neighbour)) { seen.add(neighbour); next.add(neighbour); }
      }
    }
    frontier = next;
  }
  return seen;
}

export interface Reach {
  contacted: Set<string>;
  via: Map<string, GraphNode[]>;
}

/**
 * Who a live thread could plausibly reach.
 *
 * For each uncontacted supervisor, the people already written to who sit one
 * edge away. This is the page's reason to exist: the Outreach screen can say
 * how many were written to, but only the graph can say who is *next to* them.
 */
export function secondWave(
  nodes: readonly GraphNode[],
  adjacency: Adjacency,
  byId: Map<string, GraphNode>,
): Reach {
  const contacted = new Set(nodes.filter((n) => n.contacted).map((n) => n.id));
  const via = new Map<string, GraphNode[]>();
  for (const id of contacted) {
    for (const neighbour of (adjacency.get(id) || new Map()).keys()) {
      if (contacted.has(neighbour)) continue;
      if (!via.has(neighbour)) via.set(neighbour, []);
      const source = byId.get(id);
      if (source) via.get(neighbour)!.push(source);
    }
  }
  return { contacted, via };
}

export interface Lens {
  key: string;
  title: string;
  label: string;
  hint: string;
  ids: Set<string>;
  reason: (node: GraphNode) => string;
  blank: string;
}

/** The findings, each of which is also a filter. */
export function buildLenses(
  nodes: readonly GraphNode[],
  edges: readonly GraphEdge[],
  byId: Map<string, GraphNode>,
  reach: Reach,
): Lens[] {
  const out: Lens[] = [];

  if (reach.via.size) {
    out.push({
      key: 'second', title: 'Reachable through a live thread',
      label: 'reachable through a thread',
      hint: 'Not yet written to, but one edge from someone who was. A reply is '
          + 'an introduction these people are already close to.',
      ids: new Set(reach.via.keys()),
      reason: (node) => {
        const people = reach.via.get(node.id) || [];
        return `via ${people.slice(0, 2).map((p) => shortName(p.name)).join(', ')}`
          + (people.length > 2 ? ` +${people.length - 2}` : '');
      },
      blank: 'Nothing adjacent to a thread in this graph.',
    });
  }

  const funded = nodes.filter((n) => n.funded_slots && !n.contacted);
  out.push({
    key: 'funded', title: 'Funded slots, not yet written to',
    label: 'funded and unwritten',
    hint: 'A slot with money attached that no message has gone to yet.',
    ids: new Set(funded.map((n) => n.id)),
    reason: (node) => `${node.center} · ${num(node.opportunities)} open plans`,
    blank: 'Every funded supervisor here has been written to.',
  });

  const crossing = new Set<string>();
  for (const edge of edges) {
    const a = byId.get(edge.source);
    const b = byId.get(edge.target);
    if (a && b && a.center && b.center && a.center !== b.center) {
      crossing.add(a.id);
      crossing.add(b.id);
    }
  }
  out.push({
    key: 'cross', title: 'Working across the two units',
    label: 'span ICBS and FAMED',
    hint: 'Tied to someone in the other faculty. Whether the work travels '
        + 'between the units, or stays inside one, changes what an approach can claim.',
    ids: crossing,
    reason: (node) => `${node.center} · ${num(node.degree)} ties here`,
    blank: 'No tie in this graph joins the two units.',
  });

  const alone = nodes.filter((n) => !n.degree);
  if (alone.length) {
    out.push({
      key: 'alone', title: 'Connected to nobody here',
      label: 'unconnected here',
      hint: 'No tie under this edge meaning. Try another question before '
          + 'concluding anything — co-authorship and shared technique disagree often.',
      ids: new Set(alone.map((n) => n.id)),
      reason: (node) => `${node.center} · ${num(node.publications)} publications`,
      blank: '',
    });
  }

  // Ranked fit is the fallback when nothing has been sent from this database,
  // so the rail still opens on an answer rather than an instruction.
  if (!reach.via.size) {
    out.unshift({
      key: 'fit', title: 'Strongest fits',
      label: 'ranked by fit',
      hint: 'Ranked against the profile, before any of the structural questions.',
      ids: new Set(nodes.filter((n) => Number.isFinite(n.current_pct as number)).map((n) => n.id)),
      reason: (node) => `${node.center} · ${num(node.opportunities)} open plans`,
      blank: 'Nothing profiled yet.',
    });
  }
  return out;
}

export function matching(nodes: readonly GraphNode[], query: string): Set<string> | null {
  const needle = query.trim().toLowerCase();
  if (!needle) return null;
  const hits = new Set<string>();
  for (const node of nodes) {
    if (`${node.name} ${node.department} ${node.center}`.toLowerCase().includes(needle)) {
      hits.add(node.id);
    }
  }
  return hits;
}
