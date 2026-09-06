/* What the drawing is allowed to say, and how.
 *
 * Four questions sit on top, each of which sets every encoding at once. The
 * controls underneath are "edge meaning", "colour by", "size by" — the
 * vocabulary of the data model, which is the wrong vocabulary to open with.
 * Nobody arrives wanting cosine similarity over topic-share vectors; they
 * arrive wanting to know where the campaign has landed, or who works the way
 * they work. The Display panel is still there for anyone who wants to take the
 * encodings apart by hand.
 */

import type { GraphNode, ThreadState } from '../../api/types';
import { STATE_LABEL } from '../../components/records';
import { SERIES } from '../../components/charts';
import type { SimLink, SimNode } from './simulation';

export const GROUP_BY = [
  ['center', 'Centre'],
  ['state', 'Campaign state'],
  ['none', 'Nothing — one field'],
] as const;

export const COLOUR_BY = [
  ['state', 'Campaign state'],
  ['center', 'Centre'],
  ['fit', 'Overall fit'],
  ['methods_pct', 'Methods fit'],
] as const;

/* Edges get one channel, and it is spent on something position cannot already
 * say. "Crosses the units" was here and was removed: with the units drawn as
 * territory, an edge crossing the seam is visible as a line crossing the seam,
 * and colouring it too is a second encoding of a fact already on screen. */
export const EDGE_BY = [
  ['strength', 'Tie strength'],
  ['reach', 'Campaign reach'],
] as const;

export const SIZE_BY = [
  ['degree', 'Connections here'],
  ['funded_slots', 'Funded slots'],
  ['opportunities', 'Open plans'],
  ['publications', 'Publications'],
  ['orientations', 'Supervisions'],
  ['external_collaborators', 'External co-authors'],
] as const;

const STATE_TONE: Record<ThreadState, string> = {
  awaiting: 'var(--accent)',
  replied: 'var(--accent)',
  open: 'var(--accent)',
  indicated: 'var(--good)',
  declined: 'var(--bad)',
  closed: 'var(--bad)',
};

export interface Ask {
  key: string;
  label: string;
  hint: string;
  mode: string;
  colour: string;
  size: string;
  edge: string;
}

export const ASKS: readonly Ask[] = [
  {
    key: 'reach', label: "Where I've reached",
    hint: 'Who has been written to, and which ties bridge to someone who has not.',
    mode: 'collaboration', colour: 'state', size: 'funded_slots', edge: 'reach',
  },
  {
    key: 'methods', label: 'Who works like me',
    hint: 'Shared technique. Two supervisors who have never met can be close here.',
    mode: 'skills', colour: 'fit', size: 'funded_slots', edge: 'strength',
  },
  {
    key: 'subjects', label: 'Who studies like me',
    hint: 'Subject adjacency, derived from the plans each supervisor is offering.',
    mode: 'topics', colour: 'fit', size: 'funded_slots', edge: 'strength',
  },
  {
    key: 'social', label: 'Who works together',
    hint: 'Co-authorship inside this faculty. Sparse, factual, and social rather than thematic.',
    mode: 'collaboration', colour: 'center', size: 'degree', edge: 'strength',
  },
];

/** The value a node is grouped or coloured by, as a printable string. */
export function facetOf(node: GraphNode, key: string): string {
  if (key === 'none') return 'all';
  if (key === 'state') {
    return node.contacted
      ? (node.state ? STATE_LABEL[node.state] : 'written to')
      : 'not contacted';
  }
  return (node as unknown as Record<string, string>)[key] || '—';
}

export interface EdgeStyle {
  stroke: string;
  opacity: number;
  width: number;
  dash?: string;
}

/**
 * What an edge is allowed to say.
 *
 * Thickness always carries tie strength — it is the one quantity every edge
 * has. Colour is held back for what the drawing cannot otherwise show: which
 * ties lead somewhere the campaign has not been. A reach bridge — one end
 * written to, the other not — is drawn heaviest on purpose, because that edge
 * is the whole argument for a second wave.
 */
export function edgeStyle(link: SimLink, key: string, weight: number): EdgeStyle {
  const thickness = 0.6 + 2.4 * weight;
  if (key === 'reach') {
    const touched = (link.a.contacted ? 1 : 0) + (link.b.contacted ? 1 : 0);
    if (touched === 2) return { stroke: 'var(--good)', opacity: 0.75, width: thickness };
    if (touched === 1) {
      return { stroke: 'var(--accent)', opacity: 0.95, width: thickness + 1.1 };
    }
    return { stroke: 'var(--border-strong)', opacity: 0.22, width: thickness, dash: '3 4' };
  }
  return { stroke: 'var(--border-strong)', opacity: 0.55, width: thickness };
}

export function sizeScale(nodes: readonly GraphNode[], key: string) {
  const value = (node: GraphNode) =>
    Number((node as unknown as Record<string, unknown>)[key]) || 0;
  const hi = Math.max(...nodes.map(value), 1);
  return (node: GraphNode) => 5 + 12 * Math.sqrt(value(node) / hi);
}

export interface NodeTone { fill: string; opacity: number }

export function colourFor(nodes: readonly GraphNode[], key: string): (node: GraphNode) => NodeTone {
  if (key === 'state') {
    return (node) => ({
      fill: node.contacted
        ? (node.state ? STATE_TONE[node.state] : 'var(--accent)')
        : 'var(--surface-3)',
      opacity: node.contacted ? 0.92 : 0.85,
    });
  }
  if (key === 'fit' || key === 'methods_pct') {
    // A sequential ramp on one hue: the question is "are the strong matches
    // concentrated in one part of the graph", and eight categorical colours
    // would answer a different one.
    const field = key === 'fit' ? 'current_pct' : 'methods_pct';
    return (node) => {
      const value = Number((node as unknown as Record<string, unknown>)[field]);
      if (!Number.isFinite(value)) return { fill: 'var(--surface-3)', opacity: 0.8 };
      return { fill: 'var(--accent)', opacity: Number((0.14 + 0.86 * (value / 100)).toFixed(2)) };
    };
  }
  const names = [...new Set(nodes.map((n) => facetOf(n, key)))].sort();
  const map = new Map(names.map((v, i) => [v, `var(${SERIES[i % SERIES.length]})`]));
  return (node) => ({ fill: map.get(facetOf(node, key)) || 'var(--surface-3)', opacity: 0.88 });
}

export function tooltipBits(node: SimNode): string[] {
  return [
    node.center || '',
    `${node.degree} connection${node.degree === 1 ? '' : 's'} here`,
    node.funded_slots
      ? `${node.funded_slots} funded slot${node.funded_slots === 1 ? '' : 's'}`
      : '',
    Number.isFinite(node.current_pct as number) ? `fit p${Math.round(node.current_pct!)}` : '',
    node.contacted ? (node.state ? STATE_LABEL[node.state] : 'written to') : '',
  ].filter(Boolean);
}
