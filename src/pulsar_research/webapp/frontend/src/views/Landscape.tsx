/* Research landscape — where this ecosystem's work sits, and where you sit in it.
 *
 * An earlier version of this page was a slide deck: a map, then a bar chart,
 * then a table of topic ids, then another bar chart, stacked down the page and
 * scrolled past. It described the corpus and asked nothing. Worse, the two
 * things you could click — a project on the map, a technique in the supply
 * chart — threw you onto the Work plans screen and lost the map.
 *
 * It is now the same shape as the Network screen, because it is the same job:
 * a field of things on the left, the thing you are reading on the right, and
 * the page's conclusions across the top doubling as the filters that isolate
 * them. The question it exists to answer is "which parts of this ecosystem are
 * mine, and which of them have funded work nobody has answered."
 *
 * The map still states its own distortion beside itself. A 2-D projection of a
 * high-dimensional space is a sketch of neighbourhoods, and the stress figure
 * is what says whether it is even that — so it sits in the header, not in a
 * diagnostics page nobody opens.
 */

import { useMemo } from 'react';
import { useLandscape, useOpportunities } from '../api/client';
import type { LandscapePayload, PlanRow, ProjectPoint } from '../api/types';
import { BarsH, Scatter } from '../components/charts';
import { Neighbour, Neighbours } from '../components/Neighbours';
import { Findings, useRail } from '../components/Rail';
import { RailPane } from '../components/RailPane';
import { Asks, Banner, Chip, Empty, FieldBlock, Loading } from '../components/ui';
import { num, short, shortName, sortRows, text } from '../lib/format';
import { useViewParams } from '../lib/params';
import { HeaderActions } from '../shell/Shell';

/** A project with everything actionable folded in from its work plans. */
interface Project extends ProjectPoint {
  plans: PlanRow[];
  slots: number;
  fit: number | null;
  applied: boolean;
}

/** How the map is coloured. Each is a question, not a column. */
const PAINTS = [
  { key: 'field', label: 'By field' },
  { key: 'fit', label: 'By fit to me' },
  { key: 'funding', label: 'By funded slots' },
  { key: 'reach', label: 'By campaign reach' },
] as const;
type Paint = typeof PAINTS[number]['key'];

/**
 * The colour band a point falls in.
 *
 * The scatter derives its legend from the same value it colours by, so a colour
 * and its key cannot drift apart — which is the usual way a chart ends up lying
 * about itself.
 */
function paintOf(project: Project, kind: Paint): string {
  if (kind === 'fit') {
    if ((project.fit ?? -1) >= 90) return 'top decile';
    if ((project.fit ?? -1) >= 50) return 'upper half';
    return Number.isFinite(project.fit) ? 'lower half' : 'not profiled';
  }
  if (kind === 'funding') {
    if (project.slots >= 3) return '3+ funded slots';
    return project.slots >= 1 ? '1–2 funded slots' : 'no funded slot';
  }
  if (kind === 'reach') return project.applied ? 'already applied' : 'not applied';
  return project.topic_label || 'no dominant field';
}

interface Lens {
  key: string;
  title: string;
  label: string;
  hint: string;
  has: (project: Project) => boolean;
  count: number;
}

export function Landscape() {
  const params = useViewParams();
  const rail = useRail();

  const facet = params.pick<'domain' | 'methods'>('facet', ['domain', 'methods'], 'domain');
  const paint = params.pick<Paint>('paint', PAINTS.map((p) => p.key), 'field');
  const lensKey = params.get('lens');
  const topic = params.get('topic');
  const skill = params.get('skill');

  const { data, error, isPending } = useLandscape(facet);
  const { data: opps, isPending: oppsPending } = useOpportunities();

  // The map is drawn per project; everything actionable — funding, fit, whether
  // a message has gone out — is attached to the work plans underneath it, so
  // they have to be folded together before anything here can be asked.
  const projects = useMemo<Project[]>(() => {
    if (!data || !opps) return [];
    const plans = new Map<string, PlanRow[]>();
    for (const row of opps.rows) {
      const key = row.project_code || row.project_title || '';
      if (!plans.has(key)) plans.set(key, []);
      plans.get(key)!.push(row);
    }
    return data.projects.map((project) => {
      const mine = plans.get(project.project_code || project.project_title || '') || [];
      const best = mine.reduce((top, r) => Math.max(top, r.rank_pct ?? -1), -1);
      return {
        ...project,
        plans: mine,
        slots: mine.reduce((sum, r) => sum + (Number(r.funded_slots) || 0), 0),
        fit: best < 0 ? null : best,
        applied: mine.some((r) => /inscrit/i.test(String(r.application_status || ''))
          && !/não/i.test(String(r.application_status || ''))),
      };
    });
  }, [data, opps]);

  const lenses = useMemo(() => buildLenses(projects), [projects]);
  const lens = lenses.find((l) => l.key === lensKey) || lenses[0];

  const shown = useMemo(() => {
    let rows = lens ? projects.filter(lens.has) : projects;
    if (topic) rows = rows.filter((p) => String(p.topic_id) === topic);
    if (skill) rows = rows.filter((p) => p.plans.some((r) => (r.skills || []).includes(skill)));
    return rows;
  }, [projects, lens, topic, skill]);

  if (isPending || oppsPending) return <Loading />;
  if (error || !data) {
    return <Banner tone="bad"><div>{String((error as Error)?.message || error)}</div></Banner>;
  }

  const openProject = (project: Project) => {
    const plan = sortRows(project.plans, 'rank_pct', 'desc')[0];
    if (plan) {
      rail.replace({
        kind: 'plan', id: plan.id_opportunity, label: short(plan.plan_title, 26),
      });
    }
  };

  return (
    <div className="split wide">
      <HeaderActions>
        {(lensKey || topic || skill) && (
          <button type="button" className="btn subtle"
                  onClick={() => { rail.reset(); params.set({ lens: '', topic: '', skill: '' }); }}>
            Clear
          </button>
        )}
      </HeaderActions>

      <div>
        <section className="panel">
          <div className="panel-head tight">
            <MapNote map={data.map} totalProjects={data.projects.length} shown={shown.length} />
            <Findings items={lenses.map((l) => ({
              key: l.key, label: l.label, count: l.count, hint: l.hint,
            }))} active={lensKey}
              onPick={(key) => { rail.reset(); params.set({ lens: key }); }} />
          </div>
          <div className="maptools">
            <Asks items={PAINTS} active={paint} onPick={(key) => params.set({ paint: key })} />
            <Asks active={facet}
                  items={[
                    { key: 'domain' as const, label: 'Subjects',
                      hint: 'Factor the corpus by what it studies' },
                    { key: 'methods' as const, label: 'Methods',
                      hint: 'Factor the corpus by how it studies it' },
                  ]}
                  onPick={(key) => params.set({ facet: key, topic: '' }, { push: true })} />
            {(topic || skill) && (
              <div className="chips">
                {topic && (
                  <Chip tone="accent" onClick={() => params.set({ topic: '' })}>
                    field: {short(labelOf(data, topic), 28)} ✕
                  </Chip>
                )}
                {skill && (
                  <Chip tone="accent" onClick={() => params.set({ skill: '' })}>
                    technique: {short(skill, 28)} ✕
                  </Chip>
                )}
              </div>
            )}
          </div>
          <div className="panel-body flush">
            {shown.length
              ? (
                <Scatter rows={shown} height={560}
                         keyOf={(p) => p.project_code || p.project_title}
                         colorBy={(p) => paintOf(p, paint)}
                         sizeBy={(p) => p.slots}
                         label={(p) => p.project_title}
                         meta={(p) => (
                           <>
                             {text(p.professor_name)} · {text(p.topic_label, 'no dominant field')}
                             <br />
                             {num(p.plans.length)} plans · {num(p.slots)} funded slots
                             {Number.isFinite(p.fit) ? ` · best fit p${num(p.fit, 0)}` : ''}
                           </>
                         )}
                         onPick={openProject} />
              )
              : <Empty>Nothing matches this combination.</Empty>}
          </div>
        </section>
      </div>

      <RailPane stack={rail} rootLabel={lens?.title ?? 'The whole ecosystem'} here="landscape"
                root={(
                  <Shortlist rows={shown} lens={lens} data={data} topic={topic} skill={skill}
                             params={params} onOpen={openProject} />
                )} />
    </div>
  );
}

/* ------------------------------------------------------------- the map */

function MapNote({ map, totalProjects, shown }: {
  map: LandscapePayload['map']; totalProjects: number; shown: number;
}) {
  const stress = map.stress;
  const faithful = stress === undefined || stress < 0.2;
  return (
    <p className="panel-note">
      {num(totalProjects)} projects, {num(shown)} shown.{' '}
      {text(map.method, 'MDS')} projection — stress {(stress ?? 0).toFixed(3)},
      {' '}Spearman {(map.spearman ?? 0).toFixed(3)}.{' '}
      <b>
        {faithful
          ? 'Distances are approximately faithful at this stress.'
          : 'Read position as neighbourhood, never as a ruler.'}
      </b>
    </p>
  );
}

/* ---------------------------------------------------------------- rail */

/**
 * The rail opens on the vocabulary of whatever is selected, not on an
 * instruction. Which fields the shown projects belong to, and which techniques
 * they ask for — both of them filters, so reading the ecosystem and narrowing
 * it are the same gesture.
 */
function Shortlist({ rows, lens, data, topic, skill, params, onOpen }: {
  rows: readonly Project[];
  lens: Lens | undefined;
  data: LandscapePayload;
  topic: string;
  skill: string;
  params: ReturnType<typeof useViewParams>;
  onOpen: (project: Project) => void;
}) {
  const byField = tally(rows, (p) => p.topic_label || 'no dominant field');
  const skillCounts = new Map<string, number>();
  for (const project of rows) {
    for (const plan of project.plans) {
      for (const name of plan.skills || []) {
        skillCounts.set(name, (skillCounts.get(name) || 0) + 1);
      }
    }
  }

  return (
    <div>
      <div className="detail-head">
        <h2>{lens?.title ?? 'The whole ecosystem'}</h2>
        <p className="sub">
          {num(rows.length)} projects ·{' '}
          {num(rows.reduce((s, p) => s + p.slots, 0))} funded slots ·{' '}
          {num(rows.reduce((s, p) => s + p.plans.length, 0))} work plans
        </p>
      </div>
      <div className="panel-body">
        <FieldBlock title="Fields these projects belong to">
          {byField.length
            ? (
              <BarsH rows={byField.slice(0, 12)} format={(v) => num(v)}
                     onPick={(r) => {
                       const found = data.topics.find((t) => t.label === r.label);
                       const next = found && String(found.topic_id) === topic
                         ? '' : (found ? String(found.topic_id) : '');
                       params.set({ topic: next });
                     }} />
            )
            : <Empty>No dominant field recorded.</Empty>}
        </FieldBlock>

        {topic && <TopicTerms data={data} topicId={topic} />}

        {skillCounts.size > 0 && (
          <FieldBlock title="Techniques these plans ask for"
                      note={'Click one to keep only the projects whose plans ask for it. '
                          + 'Generic scholarly competencies are excluded — every plan claims '
                          + 'them, so they separate nothing.'}>
            <BarsH format={(v) => num(v)}
                   rows={[...skillCounts.entries()]
                     .map(([label, value]) => ({ label, value }))
                     .sort((a, b) => b.value - a.value).slice(0, 14)}
                   onPick={(r) => params.set({ skill: skill === r.label ? '' : r.label })} />
          </FieldBlock>
        )}

        <FieldBlock title={`Projects (${num(rows.length)})`}>
          <Neighbours>
            {sortRows(rows, 'fit', 'desc').slice(0, 60).map((project) => (
              <Neighbour key={project.project_code || project.project_title}
                         title={short(project.project_title, 46)}
                         hint={[shortName(project.professor_name),
                                `${num(project.plans.length)} plans`].join(' · ')}
                         tail={(
                           <>
                             {Number.isFinite(project.fit) && (
                               <span className="num">p{num(project.fit, 0)}</span>
                             )}
                             {project.slots > 0 && (
                               <Chip tone="good">{num(project.slots)} slot</Chip>
                             )}
                           </>
                         )}
                         onClick={() => onOpen(project)} />
            ))}
          </Neighbours>
          {!rows.length && <Empty>Nothing in this selection.</Empty>}
        </FieldBlock>
      </div>
    </div>
  );
}

function TopicTerms({ data, topicId }: { data: LandscapePayload; topicId: string }) {
  const topic = data.topics.find((t) => String(t.topic_id) === String(topicId));
  if (!topic) return null;
  return (
    <FieldBlock title={`Terms in “${text(topic.label)}”`}
                note={'A non-negative matrix factor over project documents: a basis over '
                    + 'terms that happens to be interpretable. A navigation aid, not an '
                    + 'ontology of science.'}>
      <div className="chips">
        {(topic.terms || []).slice(0, 24).map((term) => <Chip key={term}>{term}</Chip>)}
      </div>
    </FieldBlock>
  );
}

/* ------------------------------------------------------------- findings */

function buildLenses(projects: readonly Project[]): Lens[] {
  const lenses: Omit<Lens, 'count'>[] = [
    {
      key: '', title: 'The whole ecosystem', label: 'projects indexed',
      hint: 'Every project in the corpus.',
      has: () => true,
    },
    {
      key: 'funded-unapplied', title: 'Funded, not applied to',
      label: 'funded and unanswered',
      hint: 'Projects with money attached where no application of yours has gone in.',
      has: (p) => p.slots > 0 && !p.applied,
    },
    {
      key: 'strong', title: 'Where I rank in the top decile', label: 'in my top decile',
      hint: 'Projects holding at least one work plan that ranks p90 or better against the profile.',
      has: (p) => (p.fit ?? -1) >= 90,
    },
    {
      key: 'cold', title: 'Fields I have no purchase on', label: 'below my median',
      hint: 'Projects whose best-matching plan sits under the fiftieth percentile. '
          + 'Useful for seeing what this ecosystem does that you do not.',
      has: (p) => Number.isFinite(p.fit) && (p.fit as number) < 50,
    },
  ];
  return lenses
    .map((lens) => ({ ...lens, count: projects.filter(lens.has).length }))
    .filter((lens) => lens.key === '' || lens.count > 0);
}

function tally<T>(rows: readonly T[], keyOf: (row: T) => string) {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const key = keyOf(row);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return [...counts.entries()]
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value);
}

function labelOf(data: LandscapePayload, topicId: string) {
  return data.topics.find((t) => String(t.topic_id) === String(topicId))?.label ?? topicId;
}
