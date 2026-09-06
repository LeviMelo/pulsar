/* Professors — capacity now, or trajectory over a career.
 *
 * The two rankings answer different questions and are never blended: "current"
 * uses only open work plans, active projects and active research lines, so it
 * says who could supervise this today; "trajectory" uses the whole portfolio,
 * so it says who has spent years thinking about the same problems. Which one is
 * being ranked by is a visible, switchable state rather than a hidden default.
 */

import { useMemo } from 'react';
import { useProfessor, useProfessors } from '../api/client';
import type { ProfessorDetail, ProfessorRow } from '../api/types';
import { BarsH, Distribution } from '../components/charts';
import { Neighbour, Neighbours } from '../components/Neighbours';
import { useRail, type RailStack } from '../components/Rail';
import { RailPane } from '../components/RailPane';
import { nextSort, Table } from '../components/Table';
import {
  Banner, Chip, Empty, FieldBlock, Loading, Meter, Panel, Select, Tabs,
} from '../components/ui';
import { num, short, sortRows, text, type SortDir } from '../lib/format';
import { useViewParams } from '../lib/params';
import { HeaderActions } from '../shell/Shell';

const SCOPES = {
  current: {
    column: 'current_fused_pct' as const,
    button: 'Current fit',
    label: 'Current supervision capacity',
    note: 'Open work plans, active SIGAA projects and active research lines only — '
        + 'who can take this on now.',
  },
  trajectory: {
    column: 'trajectory_fused_pct' as const,
    button: 'Trajectory',
    label: 'Research trajectory',
    note: 'The whole publication and project history, weighted by specificity, recency and '
        + 'how rare the vocabulary is in this corpus, so a broad area like “Medicina” cannot '
        + 'become the reason someone was recommended.',
  },
};
type Scope = keyof typeof SCOPES;
const SCOPE_KEYS = Object.keys(SCOPES) as Scope[];

const TABS = ['evidence', 'plans', 'topics', 'network'] as const;
type Tab = typeof TABS[number];

export function Professors() {
  const params = useViewParams();
  const rail = useRail();
  const { data, error, isPending } = useProfessors();

  const scope = params.pick<Scope>('scope', SCOPE_KEYS, 'current');
  const filters = {
    q: params.get('q'),
    center: params.get('center'),
    funded: params.flag('funded'),
    sort: params.get('sort') || 'rank',
    dir: (params.get('dir') || 'desc') as SortDir,
    sel: params.get('sel'),
  };
  const column = SCOPES[scope].column;

  const rows = useMemo(() => {
    if (!data) return [];
    const needle = filters.q.trim().toLowerCase();
    const matching = data.rows.filter((row) => {
      if (filters.center && row.center !== filters.center) return false;
      if (filters.funded && !((row.funded_opportunities ?? 0) > 0)) return false;
      if (needle) {
        const hay = [row.canonical_name, row.department, row.center].join(' ').toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
    return sortRows(matching, filters.sort, filters.dir,
      filters.sort === 'rank' ? (row: ProfessorRow) => row[column] : undefined);
  }, [data, filters.q, filters.center, filters.funded, filters.sort, filters.dir, column]);

  if (isPending) return <Loading />;
  if (error || !data) {
    return <Banner tone="bad"><div>{String((error as Error)?.message || error)}</div></Banner>;
  }

  return (
    <div>
      <HeaderActions>
        <div style={{ display: 'flex', gap: '6px' }}>
          {SCOPE_KEYS.map((key) => (
            <button key={key} type="button"
                    className={`btn sm${scope === key ? ' primary' : ''}`}
                    onClick={() => params.set({ scope: key })}>
              {SCOPES[key].button}
            </button>
          ))}
        </div>
      </HeaderActions>

      <div className="toolbar">
        <label className="field grow">
          Search
          <input type="search" value={filters.q} data-role="search"
                 placeholder="Name or department…"
                 onChange={(e) => params.set({ q: e.target.value })} />
        </label>
        <Select label="Centre" value={filters.center} allLabel="All"
                options={data.centers.map((c) => [c, c] as const)}
                onChange={(v) => params.set({ center: v })} />
        <label className="check">
          <input type="checkbox" checked={filters.funded}
                 onChange={(e) => params.set({ funded: e.target.checked ? 1 : '' })} />
          Has a funded vacancy
        </label>
      </div>

      <div className="split">
        <div>
          <p className="panel-note" style={{ margin: '0 0 12px' }}>
            <b>{SCOPES[scope].label}. </b>{SCOPES[scope].note}
          </p>
          <Panel flush>
            <Table rows={rows} rowKey={(r) => r.siape} selected={filters.sel}
                   sortKey={filters.sort} sortDir={filters.dir}
                   maxHeight="calc(100vh - 330px)"
                   onSort={(key) => {
                     const next = nextSort({ key: filters.sort, dir: filters.dir }, key);
                     params.set({ sort: next.key, dir: next.dir });
                   }}
                   onSelect={(row) => { rail.reset(); params.set({ sel: row.siape }); }}
                   columns={[
                     { key: 'rank', label: 'Fit', align: 'right', width: '108px',
                       render: (r) => <Meter value={r[column]} /> },
                     { key: 'canonical_name', label: 'Professor', truncate: true,
                       render: (r) => <span>{text(r.canonical_name)}</span> },
                     { key: 'department', label: 'Department', truncate: true, width: '190px',
                       render: (r) => <span className="muted">{short(r.department, 34)}</span> },
                     { key: 'funded_slots', label: 'Slots', align: 'right', width: '66px',
                       render: (r) => (r.funded_slots
                         ? <span className="num" style={{ color: 'var(--good)', fontWeight: 600 }}>
                             {num(r.funded_slots)}
                           </span>
                         : <span className="faint">—</span>) },
                     { key: 'opportunities', label: 'Plans', align: 'right', width: '62px',
                       render: (r) => num(r.opportunities) },
                     { key: 'publications', label: 'Pubs', align: 'right', width: '62px',
                       render: (r) => num(r.publications) },
                   ]} />
          </Panel>
        </div>

        <RailPane stack={rail} here="professors" rootLabel="This professor"
                  root={<ProfessorPane siape={filters.sel} rows={data.rows} scope={scope}
                                       rail={rail} tab={params.pick<Tab>('tab', TABS, 'evidence')}
                                       onTab={(tab) => params.set({ tab })} />} />
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- detail */

function ProfessorPane({ siape, rows, scope, rail, tab, onTab }: {
  siape: string;
  rows: readonly ProfessorRow[];
  scope: Scope;
  rail: RailStack;
  tab: Tab;
  onTab: (tab: Tab) => void;
}) {
  const { data, error, isPending } = useProfessor(siape || null);

  if (!siape) {
    return (
      <div className="panel-body">
        <Empty>Select a professor to see the evidence behind their rank.</Empty>
      </div>
    );
  }
  if (isPending) return <Loading>Loading the portfolio…</Loading>;
  if (error || !data) {
    return (
      <div className="panel-body">
        <Empty>{String((error as Error)?.message || error)}</Empty>
      </div>
    );
  }

  const record = data.record;
  const summary = rows.find((r) => r.siape === siape) || ({} as ProfessorRow);

  return (
    <div>
      <div className="detail-head">
        <h2>{text(record.canonical_name)}</h2>
        <p className="sub">
          {[`SIAPE ${record.siape}`, record.center, record.department]
            .filter(Boolean).join(' · ')}
        </p>
        <div className="detail-chips">
          {record.email
            ? <Chip tone="accent">{record.email}</Chip>
            : <Chip tone="warn">No e-mail on file</Chip>}
          {summary.funded_slots
            ? <Chip tone="good">
                {num(summary.funded_slots)} funded slot{summary.funded_slots === 1 ? '' : 's'}
              </Chip>
            : <Chip>No funded vacancy</Chip>}
          <Chip>{num(summary.publications)} publications</Chip>
          {record.lattes_id && <Chip>Lattes indexed</Chip>}
        </div>
      </div>
      <Tabs items={[
        ['evidence', 'Why this professor'],
        ['plans', 'Work plans'],
        ['topics', 'Skills & topics'],
        ['network', 'Network'],
      ] as const} active={tab} onChange={onTab} />
      <div className="panel-body">
        {tab === 'evidence' && <Evidence detail={data} summary={summary} rows={rows} scope={scope} />}
        {tab === 'plans' && <Plans detail={data} rail={rail} />}
        {tab === 'topics' && <Topics detail={data} />}
        {tab === 'network' && <Collaborators detail={data} />}
      </div>
    </div>
  );
}

function Evidence({ detail, summary, rows, scope }: {
  detail: ProfessorDetail; summary: ProfessorRow; rows: readonly ProfessorRow[]; scope: Scope;
}) {
  const column = SCOPES[scope].column;
  return (
    <div>
      <FieldBlock title={`Where they sit — ${scope}`}>
        <Distribution values={rows.map((r) => r[column])} marker={summary[column]}
                      label="Percentile across all indexed supervisors" />
      </FieldBlock>
      {SCOPE_KEYS.map((key) => {
        const items = detail.evidence.filter((e) => e.scope === key);
        return (
          <FieldBlock key={key} title={SCOPES[key].label}>
            {items.length
              ? items.map((e, index) => (
                <div className="evidence" key={`${e.label}-${index}`}>
                  <b>{text(e.label)}</b>
                  <div className="meta">
                    {[e.kind, e.year ? String(e.year) : null,
                      `match ${num(e.score, 3)}`, `weight ${num(e.weight, 3)}`]
                      .filter(Boolean).join(' · ')}
                  </div>
                </div>
              ))
              : <p className="prose muted">No evidence in this scope.</p>}
          </FieldBlock>
        );
      })}
      {detail.record.profile_summary && (
        <FieldBlock title="Lattes summary">
          <div className="prose muted">{detail.record.profile_summary}</div>
        </FieldBlock>
      )}
    </div>
  );
}

function Plans({ detail, rail }: { detail: ProfessorDetail; rail: RailStack }) {
  if (!detail.opportunities.length) {
    return <Empty>No work plans in the current corpus.</Empty>;
  }
  return (
    <Neighbours>
      {detail.opportunities.map((plan) => (
        <Neighbour key={plan.id_opportunity}
                   title={short(plan.plan_title || plan.project_title, 62)}
                   hint={short(plan.edital, 34)}
                   tail={plan.has_funding
                     ? <Chip tone="good">{num(plan.funded_slots)} slot</Chip>
                     : <Chip tone="warn">unfunded</Chip>}
                   // Opens over this page, so the ranking, the filters and the
                   // record being read all survive a glance at one of their plans.
                   onClick={() => rail.push({
                     kind: 'plan', id: plan.id_opportunity,
                     label: short(plan.plan_title || plan.project_title, 26),
                   })} />
      ))}
    </Neighbours>
  );
}

function Topics({ detail }: { detail: ProfessorDetail }) {
  const specific = detail.skills.filter((s) => !s.generic).slice(0, 16);
  return (
    <div>
      <FieldBlock title="Techniques across the portfolio">
        {specific.length
          ? (
            <BarsH colorByCategory format={(v) => num(v)}
                   rows={specific.map((s) => ({
                     label: s.label, value: s.mentions, category: s.category ?? '—',
                   }))} />
          )
          : <Empty>No distinctive techniques extracted.</Empty>}
      </FieldBlock>
      {(['domain', 'methods'] as const).map((facet) => (
        <FieldBlock key={facet} title={`${facet} topics`}>
          {detail.topics[facet]?.length
            ? (
              <BarsH format={(v) => `${num(v * 100, 1)}%`}
                     rows={detail.topics[facet].slice(0, 8)
                       .map((t) => ({ label: t.label, value: t.share }))} />
            )
            : (
              <p className="prose muted">
                Professor topic shares are derived from their work plans; this
                supervisor has none in the current corpus.
              </p>
            )}
        </FieldBlock>
      ))}
    </div>
  );
}

function Collaborators({ detail }: { detail: ProfessorDetail }) {
  if (!detail.collaborators.length) {
    return <Empty>No collaboration edges recorded from Lattes.</Empty>;
  }
  return (
    <Table rows={detail.collaborators} rowKey={(r) => r.collaborator_name} columns={[
      { key: 'collaborator_name', label: 'Collaborator', truncate: true },
      { key: 'weight', label: 'Weight', align: 'right', width: '90px',
        render: (r) => num(r.weight, 2) },
    ]} />
  );
}
