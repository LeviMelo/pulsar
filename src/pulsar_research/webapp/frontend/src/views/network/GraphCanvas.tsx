/* The drawing itself.
 *
 * React owns the structure — one group per supervisor, one line per tie — and
 * the simulation owns the coordinates. The component re-renders on the
 * simulation's frame counter, which ticks only while the layout is actually
 * moving, so a settled graph costs nothing until something is clicked.
 */

import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { SERIES } from '../../components/charts';
import { hideTip, showTip } from '../../components/charts/Tooltip';
import { num, short, shortName } from '../../lib/format';
import {
  colourFor, edgeStyle, facetNames, sizeScale, tooltipBits,
} from './encodings';
import { H, W, type Simulation } from './simulation';

export interface CanvasView {
  colour: string;
  size: string;
  edge: string;
  sel: string;
  names: boolean;
}

export function GraphCanvas({ sim, view, focus, hits, onSelect }: {
  sim: Simulation;
  view: CanvasView;
  focus: ReadonlySet<string> | null;
  hits: ReadonlySet<string> | null;
  onSelect: (id: string) => void;
}) {
  // Subscribing to the frame counter is the whole bridge between the imperative
  // simulation and the declarative drawing.
  useSyncExternalStore(sim.subscribe, sim.getSnapshot, sim.getSnapshot);

  const svg = useRef<SVGSVGElement>(null);
  const pan = useRef<{ x: number; y: number; from: typeof sim.camera } | null>(null);
  const dragMoved = useRef(false);

  const radius = useMemo(() => sizeScale(sim.nodes, view.size), [sim.nodes, view.size]);
  const colours = useMemo(() => colourFor(sim.nodes, view.colour), [sim.nodes, view.colour]);

  // A size change moves the collision radii, so the arrangement is now slightly
  // wrong; a nudge lets it correct rather than leaving overlaps. Only a size
  // change: reheating on every search keystroke would make the graph twitch
  // while someone is only typing a name.
  const lastSize = useRef<string | null>(null);
  useEffect(() => {
    const changed = lastSize.current !== null && lastSize.current !== view.size;
    lastSize.current = view.size;
    sim.setRadii(radius, changed);
  }, [sim, radius, view.size]);

  useEffect(() => {
    sim.start();
  }, [sim]);

  const toLocal = (event: { clientX: number; clientY: number }) => {
    const rect = svg.current!.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * W,
      y: ((event.clientY - rect.top) / rect.height) * H,
    };
  };

  // Plain wheel must keep scrolling the page: the graph sits inside a document,
  // and a panel that silently eats the scroll wheel is the most common way an
  // embedded canvas ruins the page around it. Zoom asks for a modifier; the
  // buttons in the corner make it reachable without one.
  useEffect(() => {
    const node = svg.current;
    if (!node) return undefined;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      sim.zoomAround(toLocal(event), Math.exp(-event.deltaY * 0.0022));
    };
    node.addEventListener('wheel', onWheel, { passive: false });
    return () => node.removeEventListener('wheel', onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sim]);

  const cards = useMemo(
    () => layoutCards(sim, view.sel, focus),
    // Cards follow positions, so they are recomputed on every frame the
    // simulation publishes; `sim.getSnapshot()` is that frame number.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sim, view.sel, focus, sim.getSnapshot()],
  );

  const bandNames = [...sim.bands.keys()];
  const allNames = sim.groupNames();

  return (
    <div className="graph-wrap">
      <svg ref={svg} className="graph" viewBox={`0 0 ${W} ${H}`}
           preserveAspectRatio="xMidYMid meet" role="img"
           aria-label="Force-directed graph of supervisors"
           onPointerDown={(event) => {
             if ((event.target as Element).closest('.gnode-g')) return;
             pan.current = {
               x: event.clientX, y: event.clientY, from: { ...sim.camera },
             };
             svg.current?.setPointerCapture(event.pointerId);
           }}
           onPointerMove={(event) => {
             if (!pan.current || !svg.current) return;
             const ratio = W / svg.current.getBoundingClientRect().width;
             sim.panBy((event.clientX - pan.current.x) * ratio,
                       (event.clientY - pan.current.y) * ratio,
                       pan.current.from);
           }}
           onPointerUp={() => { pan.current = null; }}
           onPointerCancel={() => { pan.current = null; }}
           onDoubleClick={(event) => {
             if (!(event.target as Element).closest('.gnode-g')) sim.fit(null);
           }}>
        <g transform={`translate(${sim.camera.x.toFixed(2)} ${sim.camera.y.toFixed(2)}) `
                    + `scale(${sim.camera.k.toFixed(3)})`}>

          {/* Territory, drawn as full-height bands rather than as boxes. Boxes
              were the wrong shape twice over: a gutter between them turned every
              cross-unit tie into a long diagonal that read as noise, and four
              hard walls left a ridge of nodes pressed flat against the boundary. */}
          <g className="gbands">
            {bandNames.map((name, index) => {
              const band = sim.bands.get(name)!;
              const colour = `var(${SERIES[allNames.indexOf(name) % SERIES.length]})`;
              return (
                <g key={name}>
                  <rect className="gband" x={band.x0} y={0}
                        width={band.x1 - band.x0} height={H} fill={colour} />
                  {index > 0 && (
                    <line className="gseam" x1={band.x0} y1={0} x2={band.x0} y2={H} />
                  )}
                  {/* The name only: the toggle chip beside the sliders carries
                      the count, and printing it twice is the sort of duplication
                      that makes a chart look busy without saying anything more. */}
                  <text className="gband-label" x={(band.x0 + band.x1) / 2} y={H - 12}
                        textAnchor="middle" fill={colour}>
                    {name}
                  </text>
                </g>
              );
            })}
          </g>

          <g className="gedges">
            {sim.links.map((link) => {
              if (!sim.isVisible(link.a) || !sim.isVisible(link.b)) return null;
              const inside = !focus || (focus.has(link.a.id) && focus.has(link.b.id));
              const touching = Boolean(focus)
                && (link.a.id === view.sel || link.b.id === view.sel);
              const style = edgeStyle(link, view.edge, sim.norm(link.w));
              return (
                <line key={link.id}
                      className={`gedge${inside ? '' : ' dim'}${touching ? ' lit' : ''}`}
                      x1={link.a.x.toFixed(1)} y1={link.a.y.toFixed(1)}
                      x2={link.b.x.toFixed(1)} y2={link.b.y.toFixed(1)}
                      stroke={style.stroke} strokeOpacity={style.opacity}
                      strokeWidth={style.width.toFixed(2)}
                      strokeDasharray={style.dash} />
              );
            })}
          </g>

          <g>
            {sim.nodes.map((node) => {
              if (!sim.isVisible(node)) return null;
              const shown = !focus || focus.has(node.id);
              const hit = !hits || hits.has(node.id);
              const tone = colours(node);
              const marked = Boolean(focus && !view.sel && focus.has(node.id));
              // A carded node states its name inside the card; a second floating
              // label under it would only be the same word twice.
              const carded = Boolean(focus && focus.has(node.id) && view.sel);
              // A lens can single out one supervisor among sixty-five; leaving
              // them as an unlabelled dot would make the count in the chip a riddle.
              const named = !carded
                && (view.names || marked || Boolean(hits && hits.has(node.id)));
              const classes = ['gnode-g',
                (!shown || !hit) && 'dim',
                hits && hits.has(node.id) && 'hit',
                marked && 'marked',
                node.pinned && 'pinned'].filter(Boolean).join(' ');
              return (
                <g key={node.id} className={classes}
                   transform={`translate(${node.x.toFixed(1)} ${node.y.toFixed(1)})`}
                   onMouseMove={(event) => showTip(event, (
                     <>
                       <b>{node.name}</b>
                       <span className="meta">{tooltipBits(node).join(' · ')}</span>
                     </>
                   ))}
                   onMouseLeave={hideTip}
                   onClick={(event) => {
                     event.stopPropagation();
                     if (dragMoved.current) return;  // a drag must not double as a selection
                     onSelect(node.id);
                   }}
                   onDoubleClick={(event) => {
                     // Double-click releases a pin: the gesture that made it
                     // fixed should have an equally direct inverse.
                     event.stopPropagation();
                     sim.release(node);
                   }}
                   onPointerDown={(event) => {
                     event.stopPropagation();
                     dragMoved.current = false;
                     sim.beginDrag(node);
                     (event.currentTarget as SVGGElement).setPointerCapture(event.pointerId);
                   }}
                   onPointerMove={(event) => {
                     if (sim.dragging !== node) return;
                     dragMoved.current = true;
                     const point = toLocal(event);
                     sim.dragTo(point.x, point.y);
                   }}
                   onPointerUp={() => {
                     if (sim.dragging !== node) return;
                     sim.endDrag();
                     setTimeout(() => { dragMoved.current = false; }, 0);
                   }}>
                  <circle className="gnode" r={node.r.toFixed(1)}
                          fill={tone.fill} fillOpacity={tone.opacity}
                          stroke={node.id === view.sel ? 'var(--text)'
                            : node.contacted ? 'var(--text-muted)' : 'var(--surface)'}
                          strokeWidth={node.id === view.sel ? 2.6 : 1.2} />
                  <text className="glabel" textAnchor="middle"
                        y={(node.r + 11).toFixed(1)}
                        display={named ? undefined : 'none'}>
                    {shortName(node.name)}
                  </text>
                </g>
              );
            })}
          </g>

          <g className="gcards">
            {cards.map((card) => (
              <g key={card.id} className={`gcard${card.lead ? ' lead' : ''}`}>
                <line className="stem" x1={card.ax.toFixed(1)} y1={card.ay.toFixed(1)}
                      x2={card.x.toFixed(1)} y2={card.y.toFixed(1)} />
                <rect x={(card.x - card.w / 2).toFixed(1)}
                      y={(card.y - card.h / 2).toFixed(1)}
                      width={card.w.toFixed(1)} height={card.h} rx={4} />
                <text className="n" x={card.x.toFixed(1)}
                      y={(card.y - card.h / 2 + (card.subject ? 12 : 13.5)).toFixed(1)}
                      textAnchor="middle">
                  {card.label}
                </text>
                {card.subject && (
                  <text className="k" x={card.x.toFixed(1)}
                        y={(card.y - card.h / 2 + 23).toFixed(1)} textAnchor="middle">
                    {card.subject}
                  </text>
                )}
              </g>
            ))}
          </g>
        </g>
      </svg>

      <Controls sim={sim} />
      <Legend sim={sim} view={view} />
    </div>
  );
}

/* ------------------------------------------------------------- controls */

function Controls({ sim }: { sim: Simulation }) {
  const pins = sim.pinnedCount();
  return (
    <div className="graph-controls">
      <div className="gbar">
        <button type="button" className="btn sm" title="Zoom in"
                onClick={() => sim.zoomAround({ x: W / 2, y: H / 2 }, 1.3)}>+</button>
        <button type="button" className="btn sm" title="Zoom out"
                onClick={() => sim.zoomAround({ x: W / 2, y: H / 2 }, 1 / 1.3)}>−</button>
        <button type="button" className="btn sm"
                title="Frame everything (or double-click the background)"
                onClick={() => sim.fit(null)}>Fit</button>
        <button type="button" className="btn sm"
                title={sim.frozen ? 'Let the layout move again' : 'Stop the layout where it is'}
                onClick={() => sim.setFrozen(!sim.frozen)}>
          {sim.frozen ? 'Resume' : 'Freeze'}
        </button>
        <button type="button" className="btn sm" disabled={!pins}
                title={pins
                  ? `Release ${pins} hand-placed node${pins === 1 ? '' : 's'} back to the simulation`
                  : 'Drag a node to pin it in place'}
                onClick={() => sim.unpinAll()}>
          {pins ? `Unpin ${pins}` : 'Unpin'}
        </button>
      </div>
    </div>
  );
}

/** The sliders and unit toggles, which live in the Display drawer. */
export function GraphKnobs({ sim }: { sim: Simulation }) {
  useSyncExternalStore(sim.subscribe, sim.getSnapshot, sim.getSnapshot);
  const names = sim.groupNames();
  return (
    <div className="knob-row">
      <div className="gtoggles">
        {names.map((name, index) => {
          const off = sim.hidden.has(name);
          return (
            <button key={name} type="button" className={`gtoggle${off ? ' off' : ''}`}
                    title={off
                      ? `Bring ${name} back`
                      : `Hide ${name} and give its share of the frame to the rest`}
                    onClick={() => sim.toggle(name)}>
              <i style={{ background: `var(${SERIES[index % SERIES.length]})` }} />
              {name} · {sim.headcount(name)}
            </button>
          );
        })}
      </div>
      <div className="gbar gknobs">
        <Slider sim={sim} label="Spread" knob="charge"
                title="How hard every pair of supervisors pushes apart" />
        <Slider sim={sim} label="Edge length" knob="distance"
                title="How long a connection wants to be" />
        <Slider sim={sim} label="Grouping" knob="grouping"
                title="How firmly each unit is held to its own side" />
      </div>
    </div>
  );
}

function Slider({ sim, label, knob, title }: {
  sim: Simulation; label: string; knob: 'charge' | 'distance' | 'grouping'; title: string;
}) {
  return (
    <label className="gslider" title={title}>
      <span>{label}</span>
      <input type="range" min={0.35} max={2.2} step={0.05} value={sim.knobs[knob]}
             onChange={(event) => {
               sim.knobs[knob] = Number(event.target.value);
               // Reheat rather than recompute: the drawing reorganises under
               // the cursor while the slider is still being moved.
               sim.reheat(0.45);
             }} />
    </label>
  );
}

/* --------------------------------------------------------------- legend */

function Legend({ sim, view }: { sim: Simulation; view: CanvasView }) {
  const swatches: [string, string][] = [];
  if (view.colour === 'state') {
    swatches.push(
      ['var(--surface-3)', 'not contacted'],
      ['var(--accent)', 'thread open'],
      ['var(--good)', 'indicated'],
      ['var(--bad)', 'declined or closed'],
    );
  } else if (view.colour === 'fit' || view.colour === 'methods_pct') {
    swatches.push(['var(--surface-3)', 'not profiled']);
  } else {
    const names = facetNames(sim.nodes, view.colour).slice(0, 8);
    names.forEach((value, index) => {
      swatches.push([`var(${SERIES[index % SERIES.length]})`, short(String(value), 26)]);
    });
  }

  return (
    <div className="graph-legend">
      {(view.colour === 'fit' || view.colour === 'methods_pct') && (
        <>
          <span><i style={{ background: 'var(--accent)', opacity: 0.2 }} />low</span>
          <span><i style={{ background: 'var(--accent)' }} />high</span>
        </>
      )}
      {swatches.map(([colour, label]) => (
        <span key={label}><i style={{ background: colour }} />{label}</span>
      ))}
      {/* Only the two that mean something. A faint line for "neither end
          contacted" is what an unremarkable edge already looks like. */}
      {view.edge === 'reach' && (
        <>
          <span className="edgekey">
            <i style={{ background: 'var(--accent)' }} />bridges to someone unwritten
          </span>
          <span className="edgekey">
            <i style={{ background: 'var(--good)' }} />both ends written to
          </span>
        </>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- cards */

interface Card {
  id: string; x: number; y: number; w: number; h: number;
  ax: number; ay: number; label: string; subject: string; lead: boolean;
}

/**
 * When one supervisor is selected, every neighbour says who it is in place.
 *
 * The name, the funded-slot count, and two words of subject matter. Nothing
 * else: the card exists to tell you which circle is worth clicking next, and a
 * graph that has to caption every node with its whole row is a graph whose
 * encodings have failed. Subject is the exception worth the pixels — position
 * and colour can carry structure and state, but never *what someone works on*,
 * which is usually the thing that decides where to look next.
 */
function layoutCards(
  sim: Simulation,
  sel: string,
  focus: ReadonlySet<string> | null,
): Card[] {
  const lead = sel ? sim.nodes.find((n) => n.id === sel) : null;
  if (!lead || !focus || !sim.isVisible(lead)) return [];

  // Laid out radially, pushed away from the selected node, with the leftovers
  // nudged clear: stacked cards read as one label and misattribute the name.
  const members = sim.liveNodes.filter((n) => focus.has(n.id));
  members.sort((a, b) => (a.id === lead.id ? -1 : b.id === lead.id ? 1
    : Math.hypot(a.x - lead.x, a.y - lead.y) - Math.hypot(b.x - lead.x, b.y - lead.y)));

  const placed: { x: number; y: number; w: number; h: number }[] = [];
  const cards: Card[] = [];
  for (const node of members) {
    const label = shortName(node.name)
      + (node.funded_slots ? ` · ${num(node.funded_slots)}` : '');
    const topic = short((node.keywords || []).slice(0, 2).join(' · '), 30);
    const subject = topic === '—' ? '' : topic;
    const w = Math.max(label.length * 5.6, subject.length * 4.9) + 14;
    const h = subject ? 29 : 19;

    let cx = node.x;
    let cy = node.y + node.r + h / 2 + 5;
    if (node.id !== lead.id) {
      const dx = node.x - lead.x;
      const dy = node.y - lead.y;
      const d = Math.hypot(dx, dy) || 1;
      cx = node.x + (dx / d) * (node.r + w / 2 + 7);
      cy = node.y + (dy / d) * (node.r + h / 2 + 7);
    }
    for (let attempt = 1; attempt <= 10; attempt += 1) {
      if (!placed.some((r) => overlaps(r, cx, cy, w, h))) break;
      const rung = Math.ceil(attempt / 2) * (h + 4);
      cy = (node.y + node.r + h / 2 + 5) + (attempt % 2 ? rung : -rung - node.r * 2);
    }
    placed.push({ x: cx, y: cy, w, h });
    cards.push({
      id: node.id, x: cx, y: cy, w, h, ax: node.x, ay: node.y,
      label, subject, lead: node.id === lead.id,
    });
  }
  return cards;
}

function overlaps(rect: { x: number; y: number; w: number; h: number },
                  x: number, y: number, w: number, h: number) {
  return Math.abs(rect.x - x) < (rect.w + w) / 2 + 5
      && Math.abs(rect.y - y) < (rect.h + h) / 2 + 4;
}
