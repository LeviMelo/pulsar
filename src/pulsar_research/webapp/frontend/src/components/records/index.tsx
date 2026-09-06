/* The three records every screen needs: a supervisor, a work plan, a message.
 *
 * These used to be written three times over — once inside the graph, once in
 * the work-plan explorer, once in the campaign reviewer — which is why only one
 * of them was any good. A supervisor is the same supervisor whether you reached
 * them from a ranking, a map, or a co-authorship edge, so the record is built
 * here and every screen opens the same one.
 *
 * Percentiles are read from the record's own scores rather than from whatever
 * list the caller happened to come from: the same person must not appear as p86
 * on one screen and p18 on another because two callers picked different columns.
 */

import type { ReactNode } from 'react';
import { useCampaign, usePlan, useProfessor } from '../../api/client';
import type {
  CampaignDetail, PlanDetail, ProfessorDetail, ThreadState,
} from '../../api/types';
import { date, num, short, shortName, sortRows, text, tidy } from '../../lib/format';
import { BarsH } from '../charts';
import { Neighbour, Neighbours } from '../Neighbours';
import { LeaveLink, type Frame, type ProfessorTab } from '../Rail';
import {
  Chip, Empty, Field, FieldBlock, Loading, Stat, Tabs, type Tone,
} from '../ui';

export const STATE_LABEL: Record<ThreadState, string> = {
  awaiting: 'written to, no reply yet',
  replied: 'replied',
  open: 'conversation open',
  indicated: 'indicated',
  declined: 'declined',
  closed: 'closed',
};

export function stateTone(state?: ThreadState | null): Tone {
  return ({ replied: 'accent', open: 'accent', indicated: 'good',
            declined: 'bad', closed: 'bad' } as Record<string, Tone>)[state || ''] || '';
}

/** The fused percentile for one facet, from the record's own score table. */
export function percentile(record: { scores?: { facet: string; channel: string; percentile: number | null }[] },
                           facet: string): number | null {
  const row = (record.scores || []).find((s) => s.facet === facet && s.channel === 'fused');
  return row && Number.isFinite(row.percentile) ? (row.percentile as number) : null;
}

export interface RecordContext {
  /** Push another record onto the same rail. */
  open: (frame: Frame) => void;
  /** Which screen we are on, so a record does not offer to open the page it is on. */
  here?: string;
}

/* --------------------------------------------------------------- professor */

export interface ExtraTab {
  key: string;
  label: ReactNode;
  render: (record: ProfessorDetail) => ReactNode;
}

/**
 * One supervisor, in full.
 *
 * `extraTabs` lets a screen add the tab only it can produce — the graph adds
 * the neighbourhood, which no other screen knows about — without forking the
 * record itself.
 */
export function ProfessorRecord({
  detail, summary = {}, ctx, tab = 'profile', onTab, extraTabs = [],
}: {
  detail: ProfessorDetail;
  summary?: Record<string, unknown>;
  ctx: RecordContext;
  tab?: ProfessorTab;
  onTab?: (key: ProfessorTab) => void;
  extraTabs?: readonly ExtraTab[];
}) {
  const row = detail.record || {};
  const plans = detail.opportunities || [];
  const slots = plans.reduce((sum, p) => sum + (Number(p.funded_slots) || 0), 0);
  const fit = percentile(detail, 'current');
  const state = summary.state as ThreadState | undefined;
  const campaignId = summary.campaign_id as string | undefined;
  const distinctive = (detail.skills || []).filter((s) => !s.generic);

  const items: (readonly [string, ReactNode])[] = [
    ['profile', 'Profile'],
    ['plans', `Plans (${num(plans.length)})`],
    ['skills', `Techniques (${num(distinctive.length)})`],
    ['evidence', 'Why this rank'],
    ...extraTabs.map((e) => [e.key, e.label] as const),
  ];
  const extra = extraTabs.find((e) => e.key === tab);

  return (
    <div>
      <div className="detail-head">
        <h2>{text(row.canonical_name, String(summary.name ?? '—'))}</h2>
        <p className="sub">
          {[tidy(row.department), row.center].filter(Boolean).join(' · ') || '—'}
        </p>
        <div className="detail-chips">
          {slots
            ? <Chip tone="good">{num(slots)} funded slot{slots === 1 ? '' : 's'}</Chip>
            : <Chip tone="warn">No funded slot</Chip>}
          <Chip>{num(plans.length)} open plan{plans.length === 1 ? '' : 's'}</Chip>
          {Number.isFinite(fit) && <Chip tone="accent">fit p{num(fit, 0)}</Chip>}
          {state && (
            <Chip tone={state === 'indicated' ? 'good' : 'accent'}>
              {STATE_LABEL[state] || state}
            </Chip>
          )}
          {row.email && (
            <Chip title="Copy the address"
                  onClick={() => copyText(row.email!, 'Address copied')}>
              {row.email}
            </Chip>
          )}
          {/* The thread is often the reason this person is on screen at all. */}
          {campaignId && (
            <Chip tone="accent"
                  onClick={() => ctx.open({
                    kind: 'message', campaign: campaignId, siape: row.siape,
                    label: shortName(row.canonical_name),
                  })}>
              Read the message sent
            </Chip>
          )}
          {ctx.here !== 'professors' && (
            <LeaveLink label="Professors screen" to={`/professors?sel=${row.siape}`} />
          )}
        </div>
      </div>
      <Tabs items={items} active={tab} onChange={(key) => onTab?.(key)} />
      <div className="panel-body">
        {extra
          ? extra.render(detail)
          : <BuiltinTab tab={tab} detail={detail} summary={summary} ctx={ctx} />}
      </div>
    </div>
  );
}

function BuiltinTab({ tab, detail, summary, ctx }: {
  tab: ProfessorTab;
  detail: ProfessorDetail;
  summary: Record<string, unknown>;
  ctx: RecordContext;
}) {
  if (tab === 'plans') return <PlansTab detail={detail} ctx={ctx} />;
  if (tab === 'skills') return <SkillsTab detail={detail} />;
  if (tab === 'evidence') return <EvidenceTab detail={detail} />;
  return <ProfileTab detail={detail} summary={summary} />;
}

function ProfileTab({ detail, summary }: {
  detail: ProfessorDetail; summary: Record<string, unknown>;
}) {
  const facets = ([
    ['Current work', percentile(detail, 'current')],
    ['Whole trajectory', percentile(detail, 'trajectory')],
    ['Methods', percentile(detail, 'methods')],
    ['Techniques', percentile(detail, 'skills')],
  ] as [string, number | null][])
    .filter(([, value]) => Number.isFinite(value))
    .map(([label, value]) => ({ label, value: value as number }));

  const external = (detail.collaborators || []).filter((c) => !c.target_siape).length;
  const publications = summary.publications ?? detail.record?.publications;
  const orientations = summary.orientations ?? detail.record?.orientations;

  return (
    <div>
      <div className="grid cols-3" style={{ marginBottom: '14px' }}>
        <Stat label="Publications" value={num(publications)} />
        <Stat label="Supervisions" value={num(orientations)} />
        <Stat label="Co-authors" value={num(external)} foot="outside this faculty" />
      </div>
      {facets.length > 0 && (
        <FieldBlock title="Percentile against the whole faculty"
                    note={'Current work and trajectory are scored separately on purpose: a '
                        + 'supervisor can be a strong long-run match whose open plans this '
                        + 'year point somewhere else.'}>
          <BarsH rows={facets} max={100} format={(v) => `p${num(v, 0)}`} />
        </FieldBlock>
      )}
      {detail.record?.profile_summary && (
        <FieldBlock title="From the Lattes summary">
          <div className="prose">{text(detail.record.profile_summary)}</div>
        </FieldBlock>
      )}
    </div>
  );
}

function PlansTab({ detail, ctx }: { detail: ProfessorDetail; ctx: RecordContext }) {
  const rows = detail.opportunities || [];
  if (!rows.length) return <Empty>No open work plan in this cycle.</Empty>;
  return (
    <Neighbours>
      {rows.map((plan) => (
        <Neighbour key={plan.id_opportunity}
                   title={short(plan.plan_title || plan.project_title, 64)}
                   hint={[plan.edital, plan.status].filter(Boolean).join(' · ')}
                   tail={plan.has_funding
                     ? <Chip tone="good">{num(plan.funded_slots)} slot{plan.funded_slots === 1 ? '' : 's'}</Chip>
                     : <Chip tone="warn">unfunded</Chip>}
                   onClick={() => ctx.open({
                     kind: 'plan', id: plan.id_opportunity,
                     label: short(plan.plan_title || plan.project_title, 28),
                   })} />
      ))}
    </Neighbours>
  );
}

function SkillsTab({ detail }: { detail: ProfessorDetail }) {
  const all = detail.skills || [];
  if (!all.length) return <Empty>No techniques extracted from this portfolio.</Empty>;
  const distinctive = sortRows(all.filter((sk) => !sk.generic), 'mentions', 'desc');
  const generic = all.filter((sk) => sk.generic);
  return (
    <div>
      <FieldBlock title="Distinctive techniques">
        {distinctive.length
          ? (
            <div className="chips">
              {distinctive.map((sk) => (
                <Chip key={sk.label} title={sk.category ?? undefined}>
                  {sk.label} ×{num(sk.mentions)}
                </Chip>
              ))}
            </div>
          )
          : <Empty>Everything extracted here was generic.</Empty>}
      </FieldBlock>
      {generic.length > 0 && (
        <FieldBlock title="Generic competencies"
                    note={'Kept apart because "data analysis" appears in almost every '
                        + 'portfolio, and so says nothing about who to approach.'}>
          <div className="chips">
            {generic.map((sk) => <Chip key={sk.label}>{sk.label}</Chip>)}
          </div>
        </FieldBlock>
      )}
    </div>
  );
}

function EvidenceTab({ detail }: { detail: ProfessorDetail }) {
  const rows = detail.evidence || [];
  if (!rows.length) return <Empty>No ranked evidence recorded for this supervisor.</Empty>;
  return (
    <div>
      {([['current', 'Current work'], ['trajectory', 'Whole trajectory']] as const).map(
        ([scope, label]) => {
          const mine = rows.filter((r) => r.scope === scope);
          if (!mine.length) return null;
          return (
            <FieldBlock key={scope} title={label}>
              {mine.map((item, index) => (
                <div className="evidence" key={`${item.label}-${index}`}>
                  <b>{short(item.label, 140)}</b>
                  <div className="meta">
                    {[item.kind, item.year ? num(item.year) : null,
                      `score ${num(item.score, 3)}`, `weight ${num(item.weight, 2)}`]
                      .filter(Boolean).join(' · ')}
                  </div>
                </div>
              ))}
            </FieldBlock>
          );
        },
      )}
    </div>
  );
}

/* -------------------------------------------------------------- work plan */

/**
 * A work plan, read without leaving.
 *
 * Only what answers "is this worth pursuing" — the brief, the funding, the
 * techniques it asks for. The ranking breakdown and the full scraped source
 * live on the Work plans screen, and the link says so.
 */
export function PlanRecord({ detail, ctx }: { detail: PlanDetail; ctx: RecordContext }) {
  const record = detail.record || {};
  const skills = (detail.skills || []).filter((sk) => !sk.generic);
  return (
    <div>
      <div className="detail-head">
        <h2>{text(record.plan_title || record.project_title)}</h2>
        <p className="sub">
          {[record.project_code, record.professor_name, record.edital, record.area]
            .filter(Boolean).join(' · ')}
        </p>
        <div className="detail-chips">
          {record.has_funding
            ? <Chip tone="good">{num(record.funded_slots)} funded slot{record.funded_slots === 1 ? '' : 's'}</Chip>
            : <Chip tone="warn">No funded slot</Chip>}
          <Chip>{text(record.status, 'status unknown')}</Chip>
          {record.professor_siape && (
            <Chip tone="accent"
                  onClick={() => ctx.open({
                    kind: 'professor', id: record.professor_siape!,
                    label: shortName(record.professor_name),
                  })}>
              The supervisor
            </Chip>
          )}
          {ctx.here !== 'opportunities' && (
            <LeaveLink label="Work plans screen"
                       to={`/opportunities?sel=${encodeURIComponent(record.id_opportunity)}`} />
          )}
        </div>
      </div>
      <div className="panel-body">
        <Field label="Objectives" value={record.objectives} />
        <Field label="Methodology" value={record.methodology} />
        {skills.length > 0 && (
          <FieldBlock title="Techniques extracted from this plan">
            <div className="chips">
              {skills.map((sk) => (
                <Chip key={sk.label} title={sk.category ?? undefined}>
                  {sk.label} ×{num(sk.mentions)}
                </Chip>
              ))}
            </div>
          </FieldBlock>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- message */

/**
 * What was actually sent to one supervisor.
 *
 * Every draft is individually written, so "what did I say to this person" has a
 * different answer for each of them, and it is the first thing worth knowing
 * before deciding whether to push a thread. Read-only on purpose: editing and
 * sending belong on the Outreach screen, where the whole audience is in view.
 */
export function MessageRecord({ campaign, siape, ctx }: {
  campaign: CampaignDetail; siape: string; ctx: RecordContext;
}) {
  const row = (campaign.rows || []).find((r) => String(r.siape) === String(siape));
  if (!row) {
    return (
      <div className="panel-body">
        <Empty>No message to this supervisor in that campaign.</Empty>
      </div>
    );
  }
  return (
    <div>
      <div className="detail-head">
        <h2>{text(row.subject)}</h2>
        <p className="sub">
          {[row.email, row.sent_at ? `sent ${date(row.sent_at)}` : 'not sent']
            .filter(Boolean).join(' · ')}
        </p>
        <div className="detail-chips">
          {row.state && (
            <Chip tone={stateTone(row.state)}>{STATE_LABEL[row.state] || row.state}</Chip>
          )}
          {row.is_customized && <Chip>edited by hand</Chip>}
          {row.error && <Chip tone="bad">{short(row.error, 40)}</Chip>}
          {ctx.here !== 'campaigns' && (
            <LeaveLink label="Outreach screen"
                       to={`/campaigns?c=${encodeURIComponent(campaign.campaign_id)}&sel=${siape}`} />
          )}
        </div>
      </div>
      <div className="panel-body">
        <pre className="pre">{text(row.body_text, 'This draft is empty.')}</pre>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- shared */

/**
 * Resolve a pushed frame into a record.
 *
 * Every screen's rail renders this, so a professor opened from the map and a
 * professor opened from a co-authorship edge are the same record with the same
 * tabs. Each branch is its own component because each fetches its own payload,
 * and switching kinds must remount rather than reorder hooks.
 */
export function FrameView({ frame, ctx, onTab, extraTabs }: {
  frame: Frame;
  ctx: RecordContext;
  onTab?: (key: ProfessorTab) => void;
  extraTabs?: (detail: ProfessorDetail) => readonly ExtraTab[];
}) {
  if (frame.kind === 'professor') {
    return (
      <ProfessorFrame frame={frame} ctx={ctx} onTab={onTab} extraTabs={extraTabs} />
    );
  }
  if (frame.kind === 'plan') return <PlanFrame id={frame.id} ctx={ctx} />;
  return <MessageFrame campaign={frame.campaign} siape={frame.siape} ctx={ctx} />;
}

function ProfessorFrame({ frame, ctx, onTab, extraTabs }: {
  frame: Extract<Frame, { kind: 'professor' }>;
  ctx: RecordContext;
  onTab?: (key: ProfessorTab) => void;
  extraTabs?: (detail: ProfessorDetail) => readonly ExtraTab[];
}) {
  const { data, error, isPending } = useProfessor(frame.id);
  if (isPending) return <Loading />;
  if (error || !data) return <FrameError error={error} />;
  return (
    <ProfessorRecord detail={data} summary={frame.summary} ctx={ctx}
                     tab={frame.tab || 'profile'} onTab={onTab}
                     extraTabs={extraTabs?.(data) ?? []} />
  );
}

function PlanFrame({ id, ctx }: { id: string; ctx: RecordContext }) {
  const { data, error, isPending } = usePlan(id);
  if (isPending) return <Loading />;
  if (error || !data) return <FrameError error={error} />;
  return <PlanRecord detail={data} ctx={ctx} />;
}

function MessageFrame({ campaign, siape, ctx }: {
  campaign: string; siape: string; ctx: RecordContext;
}) {
  const { data, error, isPending } = useCampaign(campaign);
  if (isPending) return <Loading />;
  if (error || !data) return <FrameError error={error} />;
  return <MessageRecord campaign={data} siape={siape} ctx={ctx} />;
}

function FrameError({ error }: { error: unknown }) {
  return (
    <div className="panel-body">
      <Empty>{String((error as Error)?.message || error || 'Could not load this record.')}</Empty>
    </div>
  );
}

/* ---------------------------------------------------------------- clipboard */

type Toaster = (message: string, tone?: Tone) => void;
let toaster: Toaster = () => {};
export function registerToaster(fn: Toaster) { toaster = fn; }
export function notify(message: string, tone?: Tone) { toaster(message, tone); }

export function copyText(value: string, label = 'Copied') {
  navigator.clipboard.writeText(value).then(
    () => toaster(label),
    () => toaster('Could not reach the clipboard', 'bad'),
  );
}
