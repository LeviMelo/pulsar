/* A live force simulation, deliberately outside React.
 *
 * The model is velocity Verlet with a decaying activity level, not the
 * batch-and-freeze layout it replaces. That distinction is the whole
 * interaction design: forces write into a velocity, the velocity is damped
 * every frame, and activity decays toward rest — so the graph always settles,
 * but *any* interaction can put energy back in and it will re-converge from
 * wherever it now is. Dragging a node therefore drags its neighbourhood with it
 * elastically, and a slider moved mid-flight reorganises the drawing under the
 * cursor instead of waiting for a recompute.
 *
 * Positions are mutable state that changes sixty times a second, which is the
 * one kind of state React should not own. So the simulation is a plain object
 * with an external store, and the component subscribes to a frame counter: it
 * re-renders while the layout is warm and not at all once it settles.
 *
 * 65 nodes make the O(n²) repulsion free, so there is no quadtree and no reason
 * for one.
 */

import type { GraphEdge, GraphNode } from '../../api/types';

export const W = 1000;
export const H = 720;

const DECAY = 0.0228;        // ~300 frames from 1 to the rest threshold
const DAMPING = 0.62;
const REST = 0.004;

/** Repulsion per square pixel of room available per node. See `tick`. */
const CHARGE_PER_AREA = 0.016;
/** Past this, two nodes stop pushing each other. See `tick`. */
const REPULSION_RANGE = 260;
/** How far inside the frame the drawing is asked to stay. */
const MARGIN = 40;
/** Stiffness of that ask. Firm enough to hold, soft enough not to pile up. */
const CONTAINMENT = 0.12;

export interface SimNode extends GraphNode {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  pinned: boolean;
  px: number;
  py: number;
}

export interface SimLink {
  id: string;
  a: SimNode;
  b: SimNode;
  w: number;
  bias: number;
  strength: number;
}

export interface Band { x0: number; x1: number }

export interface Camera { k: number; x: number; y: number }

export type FacetKey = 'center' | 'state' | 'none' | string;

/** Seed positions from the semantic projection when the store has one. */
function seed(nodes: SimNode[]) {
  const xs = nodes.map((n) => n.x).filter(Number.isFinite);
  const ys = nodes.map((n) => n.y).filter(Number.isFinite);
  const has = xs.length > 2 && ys.length > 2;
  const spanX: [number, number] = has ? [Math.min(...xs), Math.max(...xs)] : [0, 1];
  const spanY: [number, number] = has ? [Math.min(...ys), Math.max(...ys)] : [0, 1];
  nodes.forEach((node, index) => {
    if (has && Number.isFinite(node.x) && Number.isFinite(node.y)) {
      node.x = 60 + ((node.x - spanX[0]) / (spanX[1] - spanX[0] || 1)) * (W - 120);
      node.y = 60 + ((node.y - spanY[0]) / (spanY[1] - spanY[0] || 1)) * (H - 120);
    } else {
      const angle = (index / nodes.length) * Math.PI * 2;
      node.x = W / 2 + Math.cos(angle) * (W / 4);
      node.y = H / 2 + Math.sin(angle) * (H / 4);
    }
    node.vx = 0;
    node.vy = 0;
    node.r = 6;
    node.pinned = false;
    node.px = node.x;
    node.py = node.y;
  });
}

export class Simulation {
  readonly nodes: SimNode[];

  links: SimLink[] = [];

  /** Only the visible nodes and links are simulated, so hiding a unit gives
   *  its share of the frame back to the rest rather than leaving a hole. */
  liveNodes: SimNode[];

  liveLinks: SimLink[] = [];

  bands = new Map<string, Band>();

  hidden = new Set<string>();

  groupKey: FacetKey;

  knobs = { charge: 1, distance: 1, grouping: 1 };

  camera: Camera = { k: 1, x: 0, y: 0 };

  frozen = false;

  dragging: SimNode | null = null;

  private alpha = 1;

  private target = 0;

  private running = false;

  private disposed = false;

  private settledOnce = false;

  private pendingFit: { ids: ReadonlySet<string> | null } | null = null;

  private version = 0;

  private listeners = new Set<() => void>();

  private weightLo = 0;

  private weightHi = 1;

  private facetOf: (node: GraphNode, key: FacetKey) => string;

  constructor(
    nodes: GraphNode[],
    edges: GraphEdge[],
    groupKey: FacetKey,
    facetOf: (node: GraphNode, key: FacetKey) => string,
  ) {
    this.facetOf = facetOf;
    this.groupKey = groupKey;
    this.nodes = nodes.map((n) => ({
      ...n,
      x: Number(n.x) || 0, y: Number(n.y) || 0,
      vx: 0, vy: 0, r: 6, pinned: false, px: 0, py: 0,
    }));
    seed(this.nodes);
    this.liveNodes = this.nodes;
    this.setEdges(edges);
  }

  /* ------------------------------------------------------------- store */

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  /** The frame counter. A component reading this re-renders exactly as often
   *  as the layout actually moves. */
  getSnapshot = () => this.version;

  private emit() {
    this.version += 1;
    for (const listener of this.listeners) listener();
  }

  norm(weight: number) {
    return this.weightHi === this.weightLo
      ? 0.5
      : (weight - this.weightLo) / (this.weightHi - this.weightLo);
  }

  /* -------------------------------------------------------------- ties */

  /** Adopt a new set of ties over the same people, keeping every position. */
  setEdges(edges: GraphEdge[]) {
    const byId = new Map(this.nodes.map((n) => [n.id, n]));
    this.links = edges
      .map((e) => ({
        id: `${e.source}|${e.target}`,
        a: byId.get(e.source)!, b: byId.get(e.target)!,
        w: e.weight, bias: 0.5, strength: 1,
      }))
      .filter((l) => l.a && l.b);

    const weights = this.links.map((l) => l.w);
    this.weightLo = Math.min(...weights, 0);
    this.weightHi = Math.max(...weights, 1);

    const degree = new Map(this.nodes.map((n) => [n.id, 0]));
    for (const link of this.links) {
      degree.set(link.a.id, (degree.get(link.a.id) ?? 0) + 1);
      degree.set(link.b.id, (degree.get(link.b.id) ?? 0) + 1);
    }
    // d3's link bias: a hub should not be yanked around by each of its leaves.
    for (const link of this.links) {
      const da = degree.get(link.a.id) ?? 1;
      const db = degree.get(link.b.id) ?? 1;
      link.bias = da / (da + db || 1);
      link.strength = 1 / Math.max(1, Math.min(da, db));
    }
    this.applyToggles();
  }

  /* ------------------------------------------------------------ groups */

  groupNames(): string[] {
    if (this.groupKey === 'none') return [];
    // Numeric-aware so "cluster 10" does not sort before "cluster 2", which
    // would put the bands in an order the legend disagrees with.
    return [...new Set(this.nodes.map((n) => this.facetOf(n, this.groupKey)))]
      .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  }

  headcount(name: string): number {
    return this.nodes.filter((n) => this.facetOf(n, this.groupKey) === name).length;
  }

  regroup(key: FacetKey) {
    this.groupKey = key;
    this.hidden.clear();
    this.applyToggles();
  }

  toggle(name: string) {
    if (this.hidden.has(name)) this.hidden.delete(name); else this.hidden.add(name);
    this.applyToggles();
  }

  isVisible(node: GraphNode) {
    return !this.hidden.has(this.facetOf(node, this.groupKey));
  }

  /** Recompute what is drawn and simulated after a unit is shown or hidden. */
  applyToggles() {
    const visible = this.groupNames().filter((name) => !this.hidden.has(name));
    this.bands = bandsFor(visible, this.groupKey, (name) => this.headcount(name));
    this.liveNodes = this.nodes.filter((n) => this.isVisible(n));
    this.liveLinks = this.links.filter((l) => this.isVisible(l.a) && this.isVisible(l.b));
    this.reheat(0.7);
  }

  /* ------------------------------------------------------------ physics */

  private tick() {
    this.alpha += (this.target - this.alpha) * DECAY;
    const { liveNodes, liveLinks, knobs } = this;

    // Repulsion is budgeted from the room each node actually has, not fixed.
    //
    // A fixed charge sets an equilibrium radius of roughly sqrt(C·N / πk) for N
    // nodes against a centring stiffness k — so it grows with the node count and
    // knows nothing about the frame it has to fit in. At the old -260 that
    // radius came out at about 440 against 500 of half-width: no margin at all,
    // and any real graph — isolated nodes, several components, a narrow band —
    // overflowed. Measured with the frame clamp removed, the collaboration graph
    // wanted to be 2065px wide inside a 1000px canvas, which meant a third of the
    // node positions were being set by Math.min/Math.max rather than by ties.
    // That is not a cosmetic problem: two unrelated nodes clamped into the same
    // corner read as neighbours.
    //
    // Scaling with the area available per node makes the settled extent
    // independent of how many are on screen, which is also what "hiding a unit
    // gives its share of the frame back to the rest" has to mean.
    const charge = -CHARGE_PER_AREA * ((W * H) / Math.max(1, liveNodes.length)) * knobs.charge;
    for (let i = 0; i < liveNodes.length; i += 1) {
      const a = liveNodes[i];
      for (let j = i + 1; j < liveNodes.length; j += 1) {
        const b = liveNodes[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) {
          // Coincident nodes have no direction to separate along, so one has to
          // be invented. It comes from their positions in the array, not from
          // Math.random(): the same data has to draw the same graph twice, and
          // a random nudge here — rare, but it does fire — is enough to make a
          // reload disagree with itself and a layout check flake.
          const angle = ((i * 31 + j * 17) % 360) * (Math.PI / 180);
          dx = Math.cos(angle);
          dy = Math.sin(angle);
          d2 = 1;
        }
        // Bounded range, as d3-force's `distanceMax` is. A 1/d force never
        // dies, so without this every node pushes every other one outward for
        // ever and the layout does what a 2D charge distribution does: it
        // migrates to the boundary. On a sparse graph that put half the nodes
        // in the outer margin, worst of all the ones with no ties at all —
        // nothing pulls them back, so they were the first against the wall.
        // Past the cutoff a distant node feels nothing, and the centring
        // brings it home.
        if (d2 < REPULSION_RANGE * REPULSION_RANGE) {
          const push = (charge * this.alpha) / d2;
          a.vx += dx * push; a.vy += dy * push;
          b.vx -= dx * push; b.vy -= dy * push;
        }

        // Collision, so a dense cluster stays readable instead of becoming one
        // blob: the marks carry the size encoding and must not overlap it away.
        const room = a.r + b.r + 4;
        if (d2 < room * room) {
          const d = Math.sqrt(d2) || 1;
          const overlap = ((room - d) / d) * 0.5;
          a.vx -= dx * overlap; a.vy -= dy * overlap;
          b.vx += dx * overlap; b.vy += dy * overlap;
        }
      }
    }

    for (const link of liveLinks) {
      // Read the *predicted* positions, as d3 does: solving against where the
      // endpoints are heading rather than where they were is what keeps a
      // dragged neighbourhood from oscillating.
      const dx = (link.b.x + link.b.vx) - (link.a.x + link.a.vx);
      const dy = (link.b.y + link.b.vy) - (link.a.y + link.a.vy);
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      // A heavier edge wants to be shorter, so strong ties draw visibly tighter.
      const rest = (150 - 70 * this.norm(link.w)) * knobs.distance;
      const pull = ((d - rest) / d) * this.alpha * link.strength;
      link.b.vx -= dx * pull * link.bias;
      link.b.vy -= dy * pull * link.bias;
      link.a.vx += dx * pull * (1 - link.bias);
      link.a.vy += dy * pull * (1 - link.bias);
    }

    for (const node of liveNodes) {
      const band = this.bands.get(this.facetOf(node, this.groupKey));
      const home = band ? (band.x0 + band.x1) / 2 : W / 2;
      // Bands constrain sideways, so the vertical axis has nothing holding it
      // but this: without it the repulsion simply expands until the whole graph
      // is pressed flat against the top and bottom of the frame.
      node.vx += (home - node.x) * 0.014 * knobs.grouping * this.alpha;
      node.vy += (H / 2 - node.y) * 0.055 * knobs.grouping * this.alpha;
      // The territory pushes back only from outside it. A hard wall makes a
      // ridge of nodes pressed flat against the boundary and reads as an
      // artefact; a one-sided spring lets a strongly cross-linked supervisor
      // sit near the seam, which is a real thing worth seeing.
      if (band) {
        const edge = node.r + 6;
        const left = band.x0 + edge;
        const right = band.x1 - edge;
        const strength = 0.22 * knobs.grouping;
        if (node.x < left) node.vx += (left - node.x) * strength;
        if (node.x > right) node.vx += (right - node.x) * strength;
      }

      // And the frame gets the same treatment the bands do, for the same
      // reason. The clamp below cannot be the boundary: it stops a node dead
      // at the wall, so its position stops meaning anything and a ridge forms
      // along the edge. This is a spring set a little inside the frame, and
      // with the charge budgeted and its range bounded above it is rarely
      // called on — around one node in twenty-five sits inside the margin at
      // rest, against one in four when a spring like this was the only fix.
      // What it is really for is the top of the Spread slider, where the
      // drawing genuinely does want more room than there is.
      if (node.x < MARGIN) node.vx += (MARGIN - node.x) * CONTAINMENT;
      else if (node.x > W - MARGIN) node.vx += (W - MARGIN - node.x) * CONTAINMENT;
      if (node.y < MARGIN) node.vy += (MARGIN - node.y) * CONTAINMENT;
      else if (node.y > H - MARGIN) node.vy += (H - MARGIN - node.y) * CONTAINMENT;

      if (node.pinned) {
        node.x = node.px;
        node.y = node.py;
        node.vx = 0;
        node.vy = 0;
      } else {
        node.vx *= DAMPING;
        node.vy *= DAMPING;
        node.x += node.vx;
        node.y += node.vy;
        // A last resort, so a violent drag cannot throw anything off-canvas.
        // It should never decide where a node comes to rest — `npm run
        // layout:check` fails if it does.
        node.x = Math.min(W - node.r - 2, Math.max(node.r + 2, node.x));
        node.y = Math.min(H - node.r - 2, Math.max(node.r + 20, node.y));
      }
    }
  }

  private frame = () => {
    if (this.disposed) { this.running = false; return; }
    if (!this.frozen) this.tick();
    this.emit();
    if (!this.frozen && (this.alpha > REST || this.dragging || this.target > 0)) {
      requestAnimationFrame(this.frame);
    } else {
      this.running = false;
      if (!this.settledOnce) { this.settledOnce = true; this.fit(null); }
      if (this.pendingFit) {
        const { ids } = this.pendingFit;
        this.pendingFit = null;
        this.fit(ids);
      }
    }
  };

  /** Release the animation loop. Without this the rAF chain keeps a whole
   *  graph alive after the screen that owned it has been navigated away from. */
  dispose() {
    this.disposed = true;
    this.running = false;
    this.listeners.clear();
  }

  start() {
    if (this.running || this.frozen || this.disposed) return;
    this.running = true;
    requestAnimationFrame(this.frame);
  }

  /** Put energy back in. Every control that changes the layout calls this. */
  reheat(to = 0.5) {
    this.alpha = Math.max(this.alpha, to);
    this.start();
  }

  setFrozen(frozen: boolean) {
    this.frozen = frozen;
    if (!frozen) this.reheat(0.25); else this.running = false;
    this.emit();
  }

  unpinAll() {
    for (const node of this.nodes) node.pinned = false;
    this.reheat(0.6);
    this.emit();
  }

  pinnedCount() {
    return this.nodes.filter((n) => n.pinned).length;
  }

  /* ------------------------------------------------------------ dragging */

  beginDrag(node: SimNode) {
    this.dragging = node;
    node.pinned = true;
    node.px = node.x;
    node.py = node.y;
    // Hold the simulation warm for the whole gesture: the point of dragging a
    // node in a live layout is watching its neighbours follow.
    this.target = 0.32;
    this.start();
  }

  dragTo(x: number, y: number) {
    if (!this.dragging) return;
    this.dragging.px = (x - this.camera.x) / this.camera.k;
    this.dragging.py = (y - this.camera.y) / this.camera.k;
  }

  // A dragged node stays pinned. The layout is a suggestion; an arrangement
  // made by hand outranks one the simulation would have settled into. Double
  // click the node, or release all pins, to give it back.
  endDrag() {
    this.dragging = null;
    this.target = 0;
    this.emit();
  }

  release(node: SimNode) {
    node.pinned = false;
    this.reheat(0.3);
    this.emit();
  }

  /* -------------------------------------------------------------- camera */

  zoomAround(point: { x: number; y: number }, factor: number) {
    const next = Math.min(6, Math.max(0.3, this.camera.k * factor));
    // Keep the point under the cursor fixed while the scale changes.
    this.camera.x = point.x - ((point.x - this.camera.x) / this.camera.k) * next;
    this.camera.y = point.y - ((point.y - this.camera.y) / this.camera.k) * next;
    this.camera.k = next;
    this.emit();
  }

  panBy(dx: number, dy: number, from: Camera) {
    this.camera.x = from.x + dx;
    this.camera.y = from.y + dy;
    this.emit();
  }

  /** Ease the camera onto a target: a cut between two scales is disorienting
   *  in a drawing with no landmarks. */
  private glide(to: Camera) {
    const from = { ...this.camera };
    const started = performance.now();
    const step = (now: number) => {
      const p = Math.min(1, (now - started) / 400);
      const e = p < 0.5 ? 2 * p * p : 1 - ((-2 * p + 2) ** 2) / 2;
      if (this.disposed) return;
      this.camera.k = from.k + (to.k - from.k) * e;
      this.camera.x = from.x + (to.x - from.x) * e;
      this.camera.y = from.y + (to.y - from.y) * e;
      this.emit();
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  fit(ids: ReadonlySet<string> | null) {
    let x0: number;
    let x1: number;
    let y0: number;
    let y1: number;
    if (!ids && this.bands.size) {
      // With territory on screen the frame *is* the territory; fitting the node
      // extents instead would crop a band and make its label float over nothing.
      x0 = 0; x1 = W; y0 = 0; y1 = H;
    } else {
      const points = ids ? this.nodes.filter((n) => ids.has(n.id)) : this.liveNodes;
      if (!points.length) return;
      const margin = 120;   // the neighbour cards need this much room too
      x0 = Math.min(...points.map((p) => p.x)) - margin;
      x1 = Math.max(...points.map((p) => p.x)) + margin;
      y0 = Math.min(...points.map((p) => p.y)) - margin;
      y1 = Math.max(...points.map((p) => p.y)) + margin;
    }
    const k = Math.min(3.4, Math.max(0.3, Math.min(W / (x1 - x0), H / (y1 - y0))));
    this.glide({ k, x: W / 2 - ((x0 + x1) / 2) * k, y: H / 2 - ((y0 + y1) / 2) * k });
  }

  /** A fit requested while the layout is still moving would frame stale
   *  positions, so it waits for the simulation to settle. */
  requestFit(ids: ReadonlySet<string> | null) {
    if (this.alpha > 0.05) this.pendingFit = { ids };
    else this.fit(ids);
  }

  /** Sizes come from the encoding, and a size change moves the collision radii
   *  — so the arrangement is now slightly wrong and deserves a nudge. */
  setRadii(radius: (node: SimNode) => number, nudge: boolean) {
    for (const node of this.nodes) node.r = radius(node);
    if (nudge) this.reheat(0.14); else this.emit();
  }
}

/**
 * One full-height band per group, side by side with no gap.
 *
 * A band is not a hull — a hull is drawn around wherever the nodes happened to
 * land and rewrites itself every frame, so it explains nothing. It is not a box
 * either: boxes need a gutter, and a gutter turns every cross-unit tie into a
 * long diagonal. A band gives each unit a fixed side of the screen, so "is this
 * person ICBS or FAMED" is answerable from position alone, while the only
 * boundary is a seam an edge can be seen crossing.
 *
 * Width is proportional to headcount: with 44 ICBS and 21 FAMED, equal halves
 * would leave one side crowded and the other empty for no reason.
 */
export function bandsFor(
  names: readonly string[],
  key: FacetKey,
  headcount: (name: string) => number,
): Map<string, Band> {
  const map = new Map<string, Band>();
  if (key === 'none' || names.length < 2) return map;
  const sizes = names.map((name) => Math.max(1, headcount(name)));
  const total = sizes.reduce((sum, n) => sum + n, 0);
  let cursor = 0;
  names.forEach((name, index) => {
    const width = (sizes[index] / total) * W;
    map.set(name, { x0: cursor, x1: cursor + width });
    cursor += width;
  });
  return map;
}
