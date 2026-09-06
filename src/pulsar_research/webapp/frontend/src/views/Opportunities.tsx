/* Work plans — a master/detail explorer.
 *
 * The original console showed a dataframe, then a separate dropdown to choose
 * which row to "inspect", then re-ran the entire script to show it. Here the
 * list and the record sit side by side, selection is a click, and the filters,
 * the sort and the selected plan all live in the URL, so a view can be sent to
 * someone or reopened tomorrow.
 */

import { useMemo } from 'react';
import { useOpportunities, usePlan } from '../api/client';
import type { OpportunitiesPayload, PlanDetail, PlanRow } from '../api/types';
import { BarsH, Distribution } from '../components/charts';
import { useRail } from '../components/Rail';
import { RailPane } from '../components/RailPane';
import { nextSort, Table } from '../components/Table';
import {
  Banner, Chip, Empty, Field, FieldBlock, Loading, Meter, Panel, Select, Stat, Tabs,
} from '../components/ui';
import { num, short, sortRows, shortName, text, total, type SortDir } from '../lib/format';
import { useViewParams } from '../lib/params';
import { HeaderActions } from '../shell/Shell';

const TABS = ['brief', 'rank', 'structure', 'source'] as const;
type Tab = typeof TABS[number];

export function Opportunities() {
  const params = useViewParams();
  const rail = useRail();
  const { data, error, isPending } = useOpportunities();

  const filters = {
    q: params.get('q'),
    funded: params.flag('funded'),
    center: params.get('center'),
    edital: params.get('edital'),
    skill: params.get('skill'),
    min: params.number('min', 0),
    sort: params.get('sort') || 'rank_pct',
    dir: (params.get('dir') || 'desc') as SortDir,
    sel: params.get('sel'),
  };

  const rows = useMemo(() => {
    if (!data) return [];
    const needle = filters.q.trim().toLowerCase();
    const matching = data.rows.filter((row) => {
      if (filters.funded && !row.has_funding) return false;
      if (filters.center && row.center !== filters.center) return false;
      if (filters.edital && row.edital !== filters.edital) return false;
      if (filters.skill && !(row.skills || []).includes(filters.skill)) return false;
      if (filters.min && (row.rank_pct ?? 0) < filters.min) return false;
      if (needle) {
        const hay = [row.plan_title, row.project_title, row.professor_name,
                     row.project_code, row.area].join(' ').toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
    return sortRows(matching, filters.sort, filters.dir);
  }, [data, filters.q, filters.funded, filters.center, filters.edital, filters.skill,
      filters.min, filters.sort, filters.dir]);

  if (isPending) return <Loading />;
  if (error || !data) {
    return <Banner tone="bad"><div>{String((error as Error)?.message || error)}</div></Banner>;
  }

  return (
    <div>
      <HeaderActions>
        <button type="button" className="btn subtle"
                onClick={() => params.set({
                  q: '', funded: '', center: '', edital: '', skill: '', min: '',
                })}>
          Reset filters
        </button>
      </HeaderActions>

      <Toolbar data={data} filters={filters} params={params} />

      <div className="split">
        <div>
          <div className="grid cols-4" style={{ marginBottom: '14px' }}>
            <Stat label="Matching" value={num(rows.length)} />
            <Stat label="Funded" value={num(rows.filter((r) => r.has_funding).length)} />
            <Stat label="Slots" value={num(total(rows, 'funded_slots'))} />
            <Stat label="Top decile"
                  value={num(rows.filter((r) => (r.rank_pct ?? 0) >= 90).length)} />
          </div>
          <Panel flush>
            <Table rows={rows} rowKey={(r) => r.id_opportunity} selected={filters.sel}
                   sortKey={filters.sort} sortDir={filters.dir}
                   maxHeight="calc(100vh - 340px)"
                   onSort={(key) => {
                     const next = nextSort({ key: filters.sort, dir: filters.dir }, key);
                     params.set({ sort: next.key, dir: next.dir });
                   }}
                   onSelect={(row) => {
                     rail.reset();
                     params.set({ sel: row.id_opportunity });
                   }}
                   columns={[
                     { key: 'rank_pct', label: 'Fit', align: 'right', width: '108px',
                       title: 'Overall profile percentile within this corpus',
                       render: (r) => <Meter value={r.rank_pct} /> },
                     { key: 'plan_title', label: 'Work plan', truncate: true,
                       tooltip: (r) => r.plan_title || r.project_title || '',
                       render: (r) => <span>{short(r.plan_title || r.project_title, 74)}</span> },
                     { key: 'professor_name', label: 'Professor', truncate: true, width: '180px',
                       render: (r) => <span className="muted">{text(r.professor_name)}</span> },
                     { key: 'funded_slots', label: 'Slots', align: 'right', width: '66px',
                       render: (r) => (r.has_funding
                         ? <span className="num" style={{ color: 'var(--good)', fontWeight: 600 }}>
                             {num(r.funded_slots)}
                           </span>
                         : <span className="faint">—</span>) },
                     { key: 'application_status', label: 'Application', width: '120px',
                       render: (r) => <StatusChip status={r.application_status} /> },
                   ]} />
          </Panel>
        </div>

        <RailPane stack={rail} here="opportunities" rootLabel="This work plan"
                  root={<PlanPane id={filters.sel} rows={data.rows} rail={rail}
                                  tab={params.pick<Tab>('tab', TABS, 'brief')}
                                  onTab={(tab) => params.set({ tab })} />} />
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- toolbar */

function Toolbar({ data, filters, params }: {
  data: OpportunitiesPayload;
  filters: { q: string; funded: boolean; center: string; edital: string; skill: string; min: number };
  params: ReturnType<typeof useViewParams>;
}) {
  return (
    <div className="toolbar">
      <label className="field grow">
        Search
        <input type="search" value={filters.q} data-role="search"
               placeholder="Title, professor, project code…"
               onChange={(e) => params.set({ q: e.target.value })} />
      </label>
      <Select label="Centre" value={filters.center} allLabel="All"
              options={data.centers.map((c) => [c, c] as const)}
              onChange={(v) => params.set({ center: v })} />
      <Select label="Edital" value={filters.edital} allLabel="All"
              options={data.editais.map((c) => [c, short(c, 42)] as const)}
              onChange={(v) => params.set({ edital: v })} />
      <Select label="Requires skill" value={filters.skill} allLabel="All"
              options={data.skill_options.map((c) => [c, short(c, 42)] as const)}
              onChange={(v) => params.set({ skill: v })} />
      <label className="field">
        Min fit percentile
        <input type="number" min={0} max={100} step={5} value={String(filters.min)}
               onChange={(e) => params.set({ min: Number(e.target.value || 0) || '' })} />
      </label>
      <label className="check">
        <input type="checkbox" checked={filters.funded}
               onChange={(e) => params.set({ funded: e.target.checked ? 1 : '' })} />
        Funded only
      </label>
    </div>
  );
}

function StatusChip({ status }: { status?: string | null }) {
  const value = text(status, '—');
  const applied = /inscrit/i.test(value) && !/não/i.test(value);
  return <Chip tone={applied ? 'accent' : ''}>{value}</Chip>;
}

/* ---------------------------------------------------------------- detail */

function PlanPane({ id, rows, rail, tab, onTab }: {
  id: string;
  rows: readonly PlanRow[];
  rail: ReturnType<typeof useRail>;
  tab: Tab;
  onTab: (tab: Tab) => void;
}) {
  const { data, error, isPending } = usePlan(id || null);

  if (!id) {
    return (
      <div className="panel-body">
        <Empty>Select a work plan to read its brief, its ranking and its full source.</Empty>
      </div>
    );
  }
  if (isPending) return <Loading>Loading the plan…</Loading>;
  if (error || !data) {
    return (
      <div className="panel-body">
        <Empty>{String((error as Error)?.message || error)}</Empty>
      </div>
    );
  }

  const record = data.record;
  const summary = rows.find((r) => r.id_opportunity === record.id_opportunity) || {} as PlanRow;

  return (
    <div>
      <div className="detail-head">
        <h2>{text(record.plan_title || record.project_title)}</h2>
        <p className="sub">
          {[record.project_code, record.professor_name, record.center, record.edital, record.area]
            .filter(Boolean).join(' · ')}
        </p>
        <div className="detail-chips">
          {record.has_funding
            ? <Chip tone="good">
                {num(record.funded_slots)} funded slot{record.funded_slots === 1 ? '' : 's'}
              </Chip>
            : <Chip tone="warn">No funded slot</Chip>}
          <Chip>{text(record.status, 'status unknown')}</Chip>
          {summary.professor_siape && (
            <Chip tone="accent"
                  onClick={() => rail.push({
                    kind: 'professor', id: summary.professor_siape!,
                    label: shortName(summary.professor_name),
                  })}>
              The supervisor
            </Chip>
          )}
        </div>
      </div>
      <Tabs items={[
        ['brief', 'Brief'],
        ['rank', 'Why this rank'],
        ['structure', 'Structure'],
        ['source', 'Full source'],
      ] as const} active={tab} onChange={onTab} />
      <div className="panel-body">
        {tab === 'brief' && <Brief detail={data} />}
        {tab === 'rank' && <Rank detail={data} summary={summary} rows={rows} />}
        {tab === 'structure' && <Structure detail={data} />}
        {tab === 'source' && (
          <>
            <Field label="Introduction and justification"
                   value={data.record.introduction_justification} />
            <Field label="References" value={data.record.references_text} />
          </>
        )}
      </div>
    </div>
  );
}

function Brief({ detail }: { detail: PlanDetail }) {
  const skills = (detail.skills || []).filter((s) => !s.generic);
  return (
    <div>
      <Field label="Objectives" value={detail.record.objectives} />
      <Field label="Methodology" value={detail.record.methodology} />
      <Field label="Skills the student acquires" value={detail.record.acquired_skills} />
      {skills.length > 0 && (
        <FieldBlock title="Techniques extracted from this plan">
          <div className="chips">
            {skills.map((s) => (
              <Chip key={s.label} title={s.category ?? undefined}>
                {s.label} ×{s.mentions}
              </Chip>
            ))}
          </div>
        </FieldBlock>
      )}
    </div>
  );
}

function Rank({ detail, summary, rows }: {
  detail: PlanDetail; summary: PlanRow; rows: readonly PlanRow[];
}) {
  const channels = detail.scores.filter((s) => s.facet === 'overall');
  const facets = ['domain', 'methods', 'skills']
    .map((facet) => ({
      label: facet,
      value: detail.scores.find((s) => s.facet === facet && s.channel === 'fused')?.percentile,
    }))
    .filter((r): r is { label: string; value: number } => Number.isFinite(r.value as number));

  return (
    <div>
      <p className="panel-note" style={{ marginBottom: '12px' }}>
        Channels answer different questions. Lexical channels reward literal vocabulary
        overlap; latent and neural channels reward thematic adjacency even when the wording
        differs. They are never averaged into a single score.
      </p>
      <FieldBlock title="Where this plan sits among all work plans">
        <Distribution values={rows.map((r) => r.rank_pct)} marker={summary.rank_pct}
                      label="Overall profile percentile across the corpus" />
      </FieldBlock>
      <FieldBlock title="Percentile by retrieval channel">
        <BarsH max={100} format={(v) => num(v, 0)}
               meta={(r) => `raw score ${num(
                 channels.find((c) => c.channel === r.label)?.score, 3)}`}
               rows={channels
                 .filter((c) => Number.isFinite(c.percentile as number))
                 .map((c) => ({ label: c.channel, value: c.percentile as number }))} />
      </FieldBlock>
      {facets.length > 0 && (
        <FieldBlock title="Percentile by facet">
          <BarsH rows={facets} max={100} format={(v) => num(v, 0)} />
        </FieldBlock>
      )}
    </div>
  );
}

function Structure({ detail }: { detail: PlanDetail }) {
  return (
    <div>
      {(['domain', 'methods'] as const).map((facet) => (
        <FieldBlock key={facet} title={`${facet} topics`}>
          {detail.topics[facet]?.length
            ? (
              <BarsH format={(v) => `${num(v * 100, 1)}%`}
                     rows={detail.topics[facet].slice(0, 8)
                       .map((t) => ({ label: t.label, value: t.share }))} />
            )
            : <Empty>No topic share recorded for this facet.</Empty>}
        </FieldBlock>
      ))}
    </div>
  );
}
