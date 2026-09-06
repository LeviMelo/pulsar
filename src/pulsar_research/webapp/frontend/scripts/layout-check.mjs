/* A regression check on the force layout's one silent failure mode.
 *
 * A graph always looks like a graph. If the physics starts pressing nodes
 * against the frame, nothing errors and no screenshot looks obviously wrong —
 * the drawing just quietly stops meaning anything at its edges, because a node
 * held at the wall by `Math.min` is positioned by the canvas rather than by its
 * ties, and two unrelated nodes clamped into the same corner read as
 * neighbours. That is exactly what was happening: measured with the clamp
 * removed, the layout wanted to be twice as wide as the canvas, and between a
 * tenth and a third of the nodes were sitting on the boundary.
 *
 * So the properties are pinned here rather than eyeballed. The graphs are
 * generated, not the operator's: the claim under test is that *any* graph of
 * this size settles inside its own frame, and a check that needs a populated
 * DuckDB is a check nobody runs.
 *
 *     npm run layout:check
 *
 * There is no test runner in this project and one dependency for one file is a
 * bad trade, so this is a plain script: esbuild (already here, via Vite)
 * transpiles the simulation, Node drives requestAnimationFrame as a synchronous
 * queue, and a non-zero exit is the failure.
 */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const W = 1000;
const H = 720;

/* What the check will tolerate. `atFrame` is the real property; the rest guard
 * the ways a layout can satisfy it dishonestly — by piling up just inside the
 * margin instead, by collapsing into an unreadable blob, or by never settling. */
const LIMITS = {
  atFrame: 0,          // nodes whose position the hard clamp decided
  inMargin: 28,        // per cent sitting in the outer ring at rest
  overlaps: 0,         // marks covering each other's size encoding
  minSpacing: 24,      // mean distance to a nearest neighbour, px
  frames: 900,         // must come to rest, not orbit forever
};

/* Where those numbers come from. Before the fix these cases ran at 57% in the
 * margin with 71 nodes held against the frame and five overlapping marks; the
 * three real console graphs ran at 26%. After it, the worst case here is 22%
 * (a deliberately sparse graph split across seven narrow bands) and the real
 * ones sit at 2%. The ceiling is set to catch a regression toward the old
 * behaviour, not to police the last few points — a check that fails on noise
 * gets deleted. */

let queue = [];
globalThis.requestAnimationFrame = (fn) => { queue.push(fn); return queue.length; };

/** Deterministic noise, so a failure is reproducible and a pass is not luck. */
function random(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

/**
 * A faculty-shaped graph: a few dense clusters, some ties between them, and a
 * handful of people connected to nobody. The isolates matter — they have no
 * link pulling them inward, so they are the first thing a too-strong repulsion
 * throws at the wall.
 */
function synthetic({ n, clusters, isolates, density, seed }) {
  const rand = random(seed);
  const nodes = [];
  for (let i = 0; i < n; i += 1) {
    const cluster = i < n - isolates ? i % clusters : -1;
    nodes.push({
      id: `n${i}`,
      name: `Node ${i}`,
      center: i % 2 ? 'FAMED' : 'ICBS',
      community: cluster < 0 ? 9 : cluster,
      degree: 0,
      funded_slots: i % 4,
    });
  }
  const edges = [];
  const joined = nodes.filter((node) => node.community !== 9);
  for (let i = 0; i < joined.length; i += 1) {
    for (let j = i + 1; j < joined.length; j += 1) {
      const same = joined[i].community === joined[j].community;
      if (rand() < (same ? density : density / 12)) {
        edges.push({ source: joined[i].id, target: joined[j].id, weight: 1 + rand() * 4 });
      }
    }
  }
  for (const edge of edges) {
    nodes.find((nd) => nd.id === edge.source).degree += 1;
    nodes.find((nd) => nd.id === edge.target).degree += 1;
  }
  return { nodes, edges };
}

function facetOf(node, key) {
  if (key === 'none') return 'all';
  if (key === 'community') return node.community === 9 ? 'smaller clusters' : `cluster ${node.community}`;
  return node[key] || '—';
}

function settle(Simulation, graph, group) {
  queue = [];
  const sim = new Simulation(
    graph.nodes.map((nd) => ({ ...nd })),
    graph.edges.map((e) => ({ ...e })),
    group,
    facetOf,
  );
  let frames = 0;
  while (queue.length && frames < LIMITS.frames * 4) {
    const batch = queue;
    queue = [];
    for (const fn of batch) { fn(); frames += 1; }
  }
  return { sim, frames };
}

function inspect(sim) {
  const nodes = sim.liveNodes;
  const n = nodes.length;
  const atFrame = nodes.filter((d) => {
    const pad = d.r + 2.5;
    return d.x <= pad || d.x >= W - pad || d.y <= d.r + 20.5 || d.y >= H - pad;
  }).length;
  const inMargin = nodes.filter(
    (d) => d.x < 40 || d.x > W - 40 || d.y < 40 || d.y > H - 40).length;
  let spacing = 0;
  let overlaps = 0;
  for (const a of nodes) {
    let best = Infinity;
    for (const b of nodes) {
      if (a === b) continue;
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (d < a.r + b.r) overlaps += 1;
      best = Math.min(best, d);
    }
    spacing += best;
  }
  return {
    n,
    atFrame,
    inMargin: (100 * inMargin) / n,
    overlaps: overlaps / 2,
    spacing: spacing / n,
  };
}

const CASES = [
  { label: 'sparse, ungrouped', group: 'none', graph: { n: 65, clusters: 6, isolates: 8, density: 0.16, seed: 7 } },
  { label: 'sparse, by unit', group: 'center', graph: { n: 65, clusters: 6, isolates: 8, density: 0.16, seed: 7 } },
  { label: 'sparse, by cluster', group: 'community', graph: { n: 65, clusters: 6, isolates: 8, density: 0.16, seed: 7 } },
  { label: 'dense, by cluster', group: 'community', graph: { n: 65, clusters: 4, isolates: 2, density: 0.45, seed: 11 } },
  { label: 'dense, ungrouped', group: 'none', graph: { n: 65, clusters: 4, isolates: 2, density: 0.45, seed: 11 } },
  { label: 'a unit hidden (21)', group: 'center', graph: { n: 21, clusters: 3, isolates: 3, density: 0.2, seed: 3 } },
  { label: 'one big component', group: 'none', graph: { n: 40, clusters: 1, isolates: 0, density: 0.12, seed: 5 } },
  { label: 'all isolates', group: 'none', graph: { n: 30, clusters: 2, isolates: 30, density: 0.2, seed: 13 } },
];

const work = mkdtempSync(path.join(tmpdir(), 'pulsar-layout-'));
try {
  const out = path.join(work, 'sim.mjs');
  execFileSync(
    'npx',
    ['esbuild', path.join(ROOT, 'src/views/network/simulation.ts'),
      '--format=esm', '--target=node18', `--outfile=${out}`],
    { cwd: ROOT, shell: true, stdio: 'pipe' },
  );
  const { Simulation } = await import(`file://${out.split('\\').join('/')}`);

  let failed = 0;
  for (const testCase of CASES) {
    const { sim, frames } = settle(Simulation, synthetic(testCase.graph), testCase.group);
    const got = inspect(sim);
    const problems = [];
    if (got.atFrame > LIMITS.atFrame) {
      problems.push(`${got.atFrame}/${got.n} nodes are held against the frame`);
    }
    if (got.inMargin > LIMITS.inMargin) {
      problems.push(`${got.inMargin.toFixed(0)}% pile up in the outer margin`);
    }
    if (got.overlaps > LIMITS.overlaps) problems.push(`${got.overlaps} marks overlap`);
    if (got.spacing < LIMITS.minSpacing) {
      problems.push(`nearest neighbours average ${got.spacing.toFixed(0)}px apart`);
    }
    if (frames > LIMITS.frames) problems.push(`took ${frames} frames to settle`);

    const mark = problems.length ? 'FAIL' : ' ok ';
    console.log(
      `${mark}  ${testCase.label.padEnd(20)} `
      + `frame=${got.atFrame} margin=${got.inMargin.toFixed(0)}% `
      + `spacing=${got.spacing.toFixed(0)}px frames=${frames}`,
    );
    for (const problem of problems) console.log(`      ${problem}`);
    if (problems.length) failed += 1;
  }

  if (failed) {
    console.log(`\n${failed} of ${CASES.length} layouts are being shaped by the frame `
      + 'rather than by their ties.');
    process.exit(1);
  }
  console.log(`\n${CASES.length} layouts settle inside their own frame.`);
} finally {
  rmSync(work, { recursive: true, force: true });
}
