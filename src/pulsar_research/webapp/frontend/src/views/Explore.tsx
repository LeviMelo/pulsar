/* Explore — the store, searchable.
 *
 * Forty thousand records about sixty-five people, and until this screen the
 * console could show a title list per person and nothing else. Here every
 * record is one row: a paper with its venue, a board with its candidate, a
 * student by name, an appointment with its years, a course with its term.
 * Narrow by what it is, when it was, who it belongs to, who else is named on
 * it, or any words in it; open one and keep going — to the professor, to a
 * co-author, to the institution, to the journal — without leaving.
 *
 * Search is substring, on purpose. This screen is for finding a record someone
 * half-remembers; ranking by meaning is the semantic engine's job and lives on
 * its own screens.
 */

import { useEffect, useMemo } from 'react';
import { useProfessors, useRecordFacets, useRecords } from '../api/client';
import type { FacetsPayload, Family, RecordRow } from '../api/types';
import { useRail } from '../components/Rail';
import { RailPane } from '../components/RailPane';
import { Table } from '../components/Table';
import {
  FAMILY_ORDER, FAMILY_TONE, familyLabel, formLabel, recordHint,
} from '../components/records/store';
import { Banner, Chip, Empty, Loading, Panel, Select, Stat } from '../components/ui';
import { num, short, shortName } from '../lib/format';
import { useViewParams } from '../lib/params';
import { HeaderActions } from '../shell/Shell';

const PAGE = 100;

export function Explore() {
  const params = useViewParams();
  const rail = useRail();
  const filters = {
    q: params.get('q'),
    family: params.get('family') as Family | '',
    form: params.get('form'),
    who: params.get('who'),
    person: params.get('person'),
    org: params.get('org'),
    since: params.get('since'),
    until: params.get('until'),
    page: params.number('page', 0),
    sel: params.get('sel'),
    entity: params.get('entity'),
  };

  const query = useMemo(() => ({
    q: filters.q, family: filters.family, form: filters.form, siape: filters.who,
    person: filters.person, org: filters.org, since: filters.since, until: filters.until,
    limit: PAGE, offset: filters.page * PAGE,
  }), [filters.q, filters.family, filters.form, filters.who, filters.person, filters.org,
       filters.since, filters.until, filters.page]);

  const { data, error, isPending, isPlaceholderData } = useRecords(query);
  const { data: facets } = useRecordFacets(filters.who);
  const { data: profs } = useProfessors();

  // Opening a record or an entity from the URL resets the rail to it; the
  // rail's own pushes (a co-author, their institution) stay on top of that.
  useEffect(() => {
    if (filters.sel) rail.replace({ kind: 'record', id: filters.sel });
    else if (filters.entity) rail.replace({ kind: 'entity', id: filters.entity });
    else rail.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.sel, filters.entity]);

  const professors = useMemo(() => (profs?.rows || [])
    .map((r) => [r.siape, shortName(r.canonical_name) || r.siape] as const)
    .sort((a, b) => a[1].localeCompare(b[1])), [profs]);

  const set = (patch: Record<string, string | number | null>) => params.set({ ...patch, page: null, sel: null, entity: null });

  const active = [filters.q, filters.family, filters.form, filters.who, filters.person, filters.org,
                  filters.since, filters.until].filter(Boolean).length;

  if (error) {
    return <Banner tone="bad"><div>{String((error as Error)?.message || error)}</div></Banner>;
  }

  return (
    <div>
      <HeaderActions>
        {facets?.summary && (
          <span className="faint">
            {num(facets.summary.stats.records)} records
            {facets.summary.fingerprint_current ? '' : ' · stale — run pulsar records build'}
          </span>
        )}
      </HeaderActions>

      <div className="toolbar">
        <input type="search" value={filters.q} data-role="search" className="grow"
               placeholder="Words in a title, a venue, a name, a keyword…"
               onChange={(e) => set({ q: e.target.value })} />
        <Select label="Family" value={filters.family} allLabel="Every family"
                options={FAMILY_ORDER.filter((f) => (facets?.families || []).some((x) => x.family === f))
                  .map((f) => [f, familyLabel(f)] as const)}
                onChange={(v) => set({ family: v, form: null })} />
        {filters.family && (facets?.forms || []).filter((f) => f.family === filters.family).length > 1 && (
          <Select label="Kind" value={filters.form} allLabel="Every kind"
                  options={(facets?.forms || []).filter((f) => f.family === filters.family)
                    .map((f) => [f.form, `${formLabel(f.form)} (${num(f.count)})`] as const)}
                  onChange={(v) => set({ form: v })} />
        )}
        <Select label="Professor" value={filters.who} allLabel="Everyone"
                options={professors} onChange={(v) => set({ who: v })} />
        <label className="field">
          <span>From</span>
          <input type="number" min={1950} max={2100} value={filters.since}
                 placeholder="year" style={{ width: 72 }}
                 onChange={(e) => set({ since: e.target.value })} />
        </label>
        <label className="field">
          <span>To</span>
          <input type="number" min={1950} max={2100} value={filters.until}
                 placeholder="year" style={{ width: 72 }}
                 onChange={(e) => set({ until: e.target.value })} />
        </label>
        {active > 0 && (
          <button type="button" className="btn subtle"
                  onClick={() => params.set({ q: null, family: null, form: null, who: null, person: null,
                                              org: null, since: null, until: null, page: null })}>
            Clear
          </button>
        )}
      </div>

      {(filters.person || filters.org) && (
        <div className="chips" style={{ margin: '0 0 10px' }}>
          {filters.person && <Chip tone="accent" onClick={() => set({ person: null })}>named: {filters.person} ×</Chip>}
          {filters.org && <Chip tone="accent" onClick={() => set({ org: null })}>at: {filters.org} ×</Chip>}
        </div>
      )}

      <FamilyChips facets={facets} active={filters.family} onPick={(f) => set({ family: f, form: null })} />

      <div className="split wide">
        <Panel flush
               title={data
                 ? `${num(data.total)} record${data.total === 1 ? '' : 's'}${data.total > PAGE ? ` · showing ${num(data.offset + 1)}–${num(Math.min(data.offset + PAGE, data.total))}` : ''}`
                 : 'Records'}
               actions={data && data.total > PAGE ? (
                 <span className="chips">
                   <button type="button" className="btn sm" disabled={filters.page === 0}
                           onClick={() => params.set({ page: filters.page - 1 || null })}>‹ Prev</button>
                   <button type="button" className="btn sm" disabled={data.offset + PAGE >= data.total}
                           onClick={() => params.set({ page: filters.page + 1 })}>Next ›</button>
                 </span>
               ) : undefined}>
          {isPending
            ? <Loading />
            : (
              <div style={{ opacity: isPlaceholderData ? 0.6 : 1 }}>
                <Table rows={data?.rows || []} rowKey={(r) => r.record_id} selected={filters.sel}
                       maxHeight="calc(100vh - 330px)"
                       onSelect={(r) => params.set({ sel: r.record_id, entity: null }, { push: true })}
                       emptyMessage={facets?.summary?.built === false
                         ? 'No records extracted yet — run pulsar records build.'
                         : 'Nothing matches these filters.'}
                       columns={[
                         { key: 'year', label: 'Year', width: '58px', align: 'right', sortable: false,
                           render: (r: RecordRow) => <span className="num">{r.year ?? '—'}</span> },
                         { key: 'family', label: 'What', width: '150px', sortable: false,
                           render: (r: RecordRow) => (
                             <Chip tone={FAMILY_TONE[r.family]} title={familyLabel(r.family)}>
                               {r.form && r.form !== r.family ? formLabel(r.form) : familyLabel(r.family)}
                             </Chip>
                           ) },
                         { key: 'title', label: 'Record', sortable: false, truncate: true,
                           render: (r: RecordRow) => (
                             <span>
                               <span>{short(r.title, 120)}</span>
                               <div className="muted">{recordHint(r)}</div>
                             </span>
                           ) },
                         { key: 'subject', label: 'Whose', width: '160px', sortable: false,
                           render: (r: RecordRow) => (
                             <button type="button" className="linkish"
                                     title="Only this professor's records"
                                     onClick={(e) => { e.stopPropagation(); set({ who: r.siape }); }}>
                               {shortName(r.subject)}
                             </button>
                           ) },
                       ]} />
              </div>
            )}
        </Panel>

        <RailPane stack={rail} here="explore" rootLabel="The store"
                  root={<Root facets={facets} filters={filters} set={set} />} />
      </div>
    </div>
  );
}

function FamilyChips({ facets, active, onPick }: {
  facets?: FacetsPayload; active: Family | ''; onPick: (family: Family | null) => void;
}) {
  if (!facets) return null;
  const rows = FAMILY_ORDER
    .map((f) => facets.families.find((x) => x.family === f))
    .filter((x): x is FacetsPayload['families'][number] => Boolean(x));
  return (
    <div className="famchips" style={{ marginBottom: 10 }}>
      {rows.map((f) => (
        <Chip key={f.family} tone={active === f.family ? 'accent' : ''}
              title={`${f.meaning}${f.from ? ` · ${f.from}–${f.to}` : ''}`}
              onClick={() => onPick(active === f.family ? null : f.family)}>
          {familyLabel(f.family)} <span className="num">{num(f.count)}</span>
        </Chip>
      ))}
    </div>
  );
}

/** The rail at depth zero: what the store holds, and the quickest ways in. */
function Root({ facets, filters, set }: {
  facets?: FacetsPayload;
  filters: { who: string; family: string };
  set: (patch: Record<string, string | number | null>) => void;
}) {
  if (!facets) return <Loading />;
  if (!facets.summary.built) {
    return (
      <div className="panel-body">
        <Empty>
          The archive has not been read into records yet. Run <span className="mono">pulsar records build</span>,
          then <span className="mono">pulsar graph build</span>.
        </Empty>
      </div>
    );
  }
  const stats = facets.summary.stats;
  const years = facets.years.filter((y) => !filters.family || y.family === filters.family);
  const first = years.length ? Math.min(...years.map((y) => y.year)) : null;
  const last = years.length ? Math.max(...years.map((y) => y.year)) : null;
  return (
    <div className="panel-body">
      <div className="grid cols-2" style={{ marginBottom: 14 }}>
        <Stat label={filters.who ? 'Records on file' : 'Records in the store'}
              value={num(facets.families.reduce((s, f) => s + f.count, 0))}
              foot={first && last ? `${first}–${last}` : undefined} />
        <Stat label="People named" value={num(stats.people)}
              foot={facets.summary.fingerprint_current ? 'current' : 'stale — rebuild'} />
      </div>
      <div className="field-block">
        <h4>By family</h4>
        <div className="kv">
          {facets.families.map((f) => (
            <div key={f.family} style={{ display: 'contents' }}>
              <dt>
                <button type="button" className="linkish" onClick={() => set({ family: f.family, form: null })}>
                  {familyLabel(f.family)}
                </button>
              </dt>
              <dd>
                <span className="num">{num(f.count)}</span>
                <span className="faint"> {f.from ? ` ${f.from}–${f.to}` : ''} — {f.meaning}</span>
              </dd>
            </div>
          ))}
        </div>
      </div>
      {facets.venues.length > 0 && (
        <div className="field-block">
          <h4>Where the work is published</h4>
          <div className="chips">
            {facets.venues.slice(0, 18).map((v) => (
              <Chip key={v.venue} title={`${num(v.count)} works`}
                    onClick={() => set({ q: v.venue, family: 'work' })}>
                {short(v.venue, 44)} <span className="num">{num(v.count)}</span>
              </Chip>
            ))}
          </div>
        </div>
      )}
      {facets.orgs.length > 0 && (
        <div className="field-block">
          <h4>Institutions named most</h4>
          <div className="chips">
            {facets.orgs.slice(0, 18).map((o) => (
              <Chip key={o.org} title={`${num(o.count)} records`} onClick={() => set({ org: o.org })}>
                {short(o.org, 44)} <span className="num">{num(o.count)}</span>
              </Chip>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
