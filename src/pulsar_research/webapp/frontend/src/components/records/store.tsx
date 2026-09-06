/* The records the store holds, opened one at a time.
 *
 * A record is a dated, attributable fact about a person — a paper with its
 * authors and venue, an appointment, a degree with its advisor, a board with
 * its members, a student by name. An entity is anything the graph holds a node
 * for that is not faculty: an institution, a journal, a co-author. Both open in
 * the rail like a supervisor does, so a paper's co-author, that co-author's
 * institution and the people trained there are three clicks that never leave
 * the screen you were on.
 *
 * The professor's portfolio lives here too, as three tabs the supervisor
 * record adds: everything on file (filterable, with a timeline), the people
 * around them, and the career. These are what the console could not show
 * before: it had the CV's titles and nothing of its structure.
 */

import { useMemo, useState, type ReactNode } from 'react';
import { useEntity, usePortfolio, useRecord, useRecords } from '../../api/client';
import type {
  Company, Family, PortfolioPayload, RecordDetail, RecordRow,
} from '../../api/types';
import { num, short, text } from '../../lib/format';
import { Neighbour, Neighbours } from '../Neighbours';
import { LeaveLink, type Frame } from '../Rail';
import { Chip, Empty, Field, FieldBlock, Loading, Select, Stat, type Tone } from '../ui';
import type { RecordContext } from './index';

/* ------------------------------------------------------------ vocabulary */

export const FAMILY_LABEL: Record<Family, string> = {
  work: 'Publication', technical: 'Technical', project: 'Project', line: 'Research line',
  career: 'Appointment', activity: 'Duty', degree: 'Degree', training: 'Training',
  committee: 'Board', supervision: 'Supervision', event: 'Event', award: 'Award',
  course: 'Course', language: 'Language', area: 'Area',
};

export const FAMILY_TONE: Record<Family, Tone> = {
  work: 'accent', technical: 'accent', project: 'good', line: '', career: 'info',
  activity: 'info', degree: 'info', training: '', committee: 'warn', supervision: 'good',
  event: '', award: 'warn', course: '', language: '', area: '',
};

/** Family order for chips and legends: what people look for first. */
export const FAMILY_ORDER: readonly Family[] = [
  'work', 'supervision', 'committee', 'project', 'technical', 'career', 'degree',
  'activity', 'course', 'event', 'award', 'training', 'line', 'area', 'language',
];

export function familyLabel(family: string): string {
  return FAMILY_LABEL[family as Family] ?? family;
}

export function formLabel(form: string): string {
  return form.replace(/_/g, ' ');
}

/** The one line under a record's title that says where it sits. */
export function recordHint(row: RecordRow | RecordDetail): string {
  const parts: (string | null | undefined)[] = [];
  if (row.year) parts.push(row.year_end && row.year_end !== row.year ? `${row.year}–${row.year_end}` : String(row.year));
  else if (row.year_end) parts.push(String(row.year_end));
  if (row.family === 'career' || row.family === 'degree' || row.family === 'training') {
    if (row.status) parts.push(row.status);
  }
  parts.push(row.venue || row.org || null);
  if (row.counterpart && row.family !== 'career') parts.push(row.counterpart);
  return parts.filter(Boolean).join(' · ');
}

export function frameFor(row: RecordRow | RecordDetail): Frame {
  return { kind: 'record', id: row.record_id, label: short(row.title, 28) };
}

/* ---------------------------------------------------------------- record */

export function RecordRecord({ detail, ctx }: { detail: RecordDetail; ctx: RecordContext }) {
  const payload = detail.payload || {};
  const people = detail.people || [];
  const shown = Object.entries(payload).filter(([, v]) => v !== '' && v !== null && v !== false
    && !(Array.isArray(v) && !v.length) && !(typeof v === 'object' && v && !Array.isArray(v) && !Object.keys(v).length));

  return (
    <div>
      <div className="detail-head">
        <h2>{text(detail.title)}</h2>
        <p className="sub">{recordHint(detail) || '—'}</p>
        <div className="detail-chips">
          <Chip tone={FAMILY_TONE[detail.family]}>{familyLabel(detail.family)}</Chip>
          {detail.form && detail.form !== detail.family && <Chip>{formLabel(detail.form)}</Chip>}
          {detail.nature && <Chip>{detail.nature.toLowerCase()}</Chip>}
          {detail.status && <Chip tone={detail.status === 'ongoing' || detail.status === 'current' ? 'good' : ''}>{detail.status}</Chip>}
          <Chip tone="accent"
                onClick={() => ctx.open({ kind: 'professor', id: detail.siape, label: detail.subject })}>
            {detail.subject}
          </Chip>
          {detail.doi && (
            <a className="chip" href={`https://doi.org/${detail.doi}`} target="_blank" rel="noreferrer">
              doi:{detail.doi}
            </a>
          )}
          {detail.entity_id && (
            <Chip onClick={() => ctx.open({ kind: 'entity', id: detail.entity_id!, label: short(detail.title, 24) })}>
              In the graph
            </Chip>
          )}
        </div>
      </div>
      <div className="panel-body">
        {(detail.venue || detail.org) && (
          <div className="kv">
            {detail.venue && <Field label="Venue" value={detail.venue} />}
            {detail.org && <Field label={detail.family === 'work' ? 'Publisher / funder' : 'Institution'} value={detail.org} />}
            {detail.language && <Field label="Language" value={detail.language} />}
          </div>
        )}
        {people.length > 0 && (
          <FieldBlock title={peopleTitle(detail.family)}
                      note={detail.family === 'committee' && detail.counterpart
                        ? `Candidate: ${detail.counterpart}` : undefined}>
            <Neighbours>
              {people.map((p) => (
                <Neighbour key={`${p.ordinal}-${p.name}`}
                           title={p.display || p.name}
                           hint={[p.role, p.siape ? 'faculty' : null].filter(Boolean).join(' · ')}
                           tail={p.siape ? <Chip tone="accent">faculty</Chip> : (p.entity_id ? <Chip>in graph</Chip> : null)}
                           onClick={() => {
                             if (p.siape) ctx.open({ kind: 'professor', id: p.siape, label: p.display || p.name });
                             else if (p.entity_id) ctx.open({ kind: 'entity', id: p.entity_id, label: short(p.display || p.name, 24) });
                             else ctx.open({ kind: 'search', person: p.name, label: short(p.name, 24) });
                           }} />
              ))}
            </Neighbours>
          </FieldBlock>
        )}
        {(detail.keywords.length > 0 || detail.areas.length > 0) && (
          <FieldBlock title="Keywords and areas">
            <div className="chips">
              {detail.keywords.map((k) => <Chip key={`k-${k}`}>{k}</Chip>)}
              {detail.areas.map((a) => <Chip key={`a-${a}`} tone="info">{a}</Chip>)}
            </div>
          </FieldBlock>
        )}
        {shown.length > 0 && (
          <FieldBlock title="Everything else the source said">
            <div className="kv">
              {shown.map(([key, value]) => (
                <Field key={key} label={key.replace(/_/g, ' ')} value={renderValue(value)} />
              ))}
            </div>
          </FieldBlock>
        )}
        <p className="faint mono" style={{ marginTop: 12 }}>{detail.source} · {short(detail.source_ref, 110)}</p>
      </div>
    </div>
  );
}

function peopleTitle(family: Family): string {
  return ({ work: 'Authors', technical: 'Authors', project: 'Team', committee: 'Board members',
            supervision: 'Student', degree: 'Advisors', event: 'Participants' } as Record<string, string>)[family]
    || 'People named';
}

function renderValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map((v) => (typeof v === 'object' && v ? tieLine(v as Record<string, unknown>) : String(v))).join('; ');
  }
  if (typeof value === 'object' && value) {
    return Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== '' && v !== null)
      .map(([k, v]) => `${k.replace(/_/g, ' ')} ${String(v)}`).join(' · ');
  }
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  return String(value);
}

/** An appointment's tie, as one line: "Professor Adjunto, 2015–, 40h, exclusive". */
function tieLine(tie: Record<string, unknown>): string {
  const span = tie.from || tie.to ? `${tie.from ?? '?'}–${tie.to ?? ''}` : '';
  return [tie.role || tie.kind, span, tie.hours ? `${tie.hours}h` : null,
          tie.exclusive ? 'exclusive' : null].filter(Boolean).join(', ');
}

/* ---------------------------------------------------------------- entity */

export function EntityRecord({ detail, ctx }: {
  detail: ReturnType<typeof useEntity>['data'] & object; ctx: RecordContext;
}) {
  const node = detail.entity;
  const [open, setOpen] = useState<string | null>(null);
  const metrics = Object.entries(detail.metrics || {}).filter(([k]) => !k.startsWith('faculty_'));
  return (
    <div>
      <div className="detail-head">
        <h2>{text(node.name)}</h2>
        <p className="sub">{kindLabel(node.kind, node.payload)}</p>
        <div className="detail-chips">
          <Chip>{node.kind}</Chip>
          {node.siape && (
            <Chip tone="accent" onClick={() => ctx.open({ kind: 'professor', id: node.siape!, label: node.name })}>
              Open as faculty
            </Chip>
          )}
          {node.kind === 'person' && !node.siape && (
            <Chip onClick={() => ctx.open({ kind: 'search', person: node.name, label: short(node.name, 24) })}>
              Every record naming them
            </Chip>
          )}
          {ctx.here !== 'explore' && (
            <LeaveLink label="Explore screen" to={`/explore?entity=${encodeURIComponent(node.entity_id)}`} />
          )}
        </div>
      </div>
      <div className="panel-body">
        {metrics.length > 0 && (
          <div className="grid cols-3" style={{ marginBottom: 14 }}>
            {metrics.slice(0, 6).map(([k, v]) => (
              <Stat key={k} label={k.replace(/_/g, ' ')} value={num(v, v < 1 && v > 0 ? 3 : 0)} />
            ))}
          </div>
        )}
        {detail.groups.length === 0 && detail.records.length === 0 && (
          <Empty>Nothing links to this entity yet.</Empty>
        )}
        {detail.groups.map((group) => {
          const expanded = open === group.label || detail.groups.length === 1;
          const rows = expanded ? group.rows : group.rows.slice(0, 6);
          return (
            <FieldBlock key={group.label}
                        title={`${group.label} (${num(group.count)})`}>
              <Neighbours>
                {rows.map((row) => (
                  <Neighbour key={row.entity_id}
                             title={row.name}
                             hint={[row.kind, row.year ? String(row.year) : null].filter(Boolean).join(' · ')}
                             tail={row.weight > 1 ? <Chip>{num(row.weight)}</Chip> : (row.indexed ? <Chip tone="accent">faculty</Chip> : null)}
                             onClick={() => openEntity(ctx, row.entity_id, row.name, row.indexed)} />
                ))}
              </Neighbours>
              {group.rows.length > 6 && !expanded && (
                <button type="button" className="btn" style={{ marginTop: 6 }} onClick={() => setOpen(group.label)}>
                  Show all {num(group.rows.length)}
                </button>
              )}
            </FieldBlock>
          );
        })}
        {detail.records.length > 0 && (
          <FieldBlock title={`Records naming this (${num(detail.records.length)})`}>
            <RecordList rows={detail.records} ctx={ctx} showSubject />
          </FieldBlock>
        )}
      </div>
    </div>
  );
}

function kindLabel(kind: string, payload: Record<string, unknown>): string {
  if (kind === 'org') return payload.level ? `${String(payload.level)}${payload.code ? ` · ${String(payload.code)}` : ''}` : 'institution';
  if (kind === 'venue') return `venue · ${String(payload.form || '')}`;
  if (kind === 'person') return payload.indexed ? 'indexed faculty' : 'named on records; not indexed';
  if (kind === 'work') return `${String(payload.form || 'work')}${payload.year ? ` · ${String(payload.year)}` : ''}`;
  if (kind === 'project') return `${String(payload.origin || 'project')}${payload.year ? ` · ${String(payload.year)}` : ''}`;
  return kind;
}

export function openEntity(ctx: RecordContext, entityId: string, name: string, indexed: boolean) {
  if (indexed && entityId.startsWith('person:')) {
    ctx.open({ kind: 'professor', id: entityId.slice('person:'.length), label: name });
  } else if (entityId.startsWith('work:') || entityId.startsWith('project:')) {
    ctx.open({ kind: 'entity', id: entityId, label: short(name, 24) });
  } else {
    ctx.open({ kind: 'entity', id: entityId, label: short(name, 24) });
  }
}

/* ---------------------------------------------------------- record lists */

export function RecordList({ rows, ctx, showSubject = false, limit }: {
  rows: readonly RecordRow[]; ctx: RecordContext; showSubject?: boolean; limit?: number;
}) {
  const [more, setMore] = useState(false);
  const shown = limit && !more ? rows.slice(0, limit) : rows;
  if (!rows.length) return <Empty>No records here.</Empty>;
  return (
    <div>
      <Neighbours>
        {shown.map((row) => (
          <Neighbour key={row.record_id}
                     title={short(row.title, 96)}
                     hint={[showSubject ? row.subject : null, recordHint(row)].filter(Boolean).join(' · ')}
                     tail={<Chip tone={FAMILY_TONE[row.family]}>{formLabel(row.form) || familyLabel(row.family)}</Chip>}
                     onClick={() => ctx.open(frameFor(row))} />
        ))}
      </Neighbours>
      {limit && rows.length > limit && !more && (
        <button type="button" className="btn" style={{ marginTop: 6 }} onClick={() => setMore(true)}>
          Show all {num(rows.length)}
        </button>
      )}
    </div>
  );
}

/* ----------------------------------------------------- portfolio tabs */

/** Years × families as small bars: when this person was doing what. */
export function Timeline({ rows, families, onYear, year }: {
  rows: readonly RecordRow[];
  families: readonly Family[];
  onYear?: (year: number | null) => void;
  year?: number | null;
}) {
  const counts = useMemo(() => {
    const byYear = new Map<number, Map<Family, number>>();
    for (const row of rows) {
      const start = row.year ?? row.year_end;
      if (!start) continue;
      const end = row.family === 'career' || row.family === 'activity'
        ? (row.year_end ?? new Date().getFullYear()) : start;
      for (let y = start; y <= Math.min(end, start + 60); y += 1) {
        const slot = byYear.get(y) ?? new Map<Family, number>();
        slot.set(row.family, (slot.get(row.family) ?? 0) + 1);
        byYear.set(y, slot);
      }
    }
    return byYear;
  }, [rows]);
  const years = [...counts.keys()].sort((a, b) => a - b);
  if (!years.length) return null;
  const first = years[0];
  const last = years[years.length - 1];
  const span: number[] = [];
  for (let y = first; y <= last; y += 1) span.push(y);
  const top = Math.max(...span.map((y) => sum(counts.get(y), families)), 1);
  return (
    <div className="timeline" role="img" aria-label="Records per year">
      {span.map((y) => {
        const slot = counts.get(y);
        const total = sum(slot, families);
        return (
          <button type="button" key={y} className={`tl-col${year === y ? ' on' : ''}`}
                  title={`${y}: ${total} record${total === 1 ? '' : 's'}`}
                  onClick={() => onYear?.(year === y ? null : y)}>
            <span className="tl-bar" style={{ height: `${(100 * total) / top}%` }}>
              {FAMILY_ORDER.filter((f) => families.includes(f) && slot?.get(f)).map((f) => (
                <span key={f} className={`tl-seg fam-${f}`}
                      style={{ flex: slot!.get(f)! }} />
              ))}
            </span>
            {(y % 5 === 0 || y === first || y === last) && <span className="tl-year">{y}</span>}
          </button>
        );
      })}
    </div>
  );
}

function sum(slot: Map<Family, number> | undefined, families: readonly Family[]): number {
  if (!slot) return 0;
  let total = 0;
  for (const f of families) total += slot.get(f) ?? 0;
  return total;
}

export function PortfolioTab({ siape, ctx }: { siape: string; ctx: RecordContext }) {
  const { data, error, isPending } = usePortfolio(siape);
  if (isPending) return <Loading>Reading the portfolio…</Loading>;
  if (error || !data) return <Empty>{String((error as Error)?.message || 'No portfolio.')}</Empty>;
  return <Portfolio data={data} ctx={ctx} />;
}

function Portfolio({ data, ctx }: { data: PortfolioPayload; ctx: RecordContext }) {
  const [family, setFamily] = useState<Family | ''>('');
  const [form, setForm] = useState('');
  const [year, setYear] = useState<number | null>(null);
  const [q, setQ] = useState('');

  const present = FAMILY_ORDER.filter((f) => data.records.some((r) => r.family === f));
  const forms = useMemo(() => {
    const seen = new Map<string, number>();
    for (const r of data.records) if (!family || r.family === family) seen.set(r.form, (seen.get(r.form) ?? 0) + 1);
    return [...seen.entries()].sort((a, b) => b[1] - a[1]);
  }, [data.records, family]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return data.records.filter((r) => {
      if (family && r.family !== family) return false;
      if (form && r.form !== form) return false;
      if (year !== null) {
        const start = r.year ?? r.year_end;
        const end = r.family === 'career' || r.family === 'activity' ? (r.year_end ?? 9999) : (r.year_end ?? start);
        if (!start || year < start || year > (end ?? start)) return false;
      }
      if (needle && !`${r.title} ${r.venue} ${r.org} ${r.counterpart} ${r.keywords.join(' ')}`.toLowerCase().includes(needle)) return false;
      return true;
    }).sort((a, b) => (b.year ?? 0) - (a.year ?? 0));
  }, [data.records, family, form, year, q]);

  if (!data.records.length) {
    return <Empty>Nothing extracted for this person. Run <code>pulsar records build</code>.</Empty>;
  }

  return (
    <div>
      <div className="famchips">
        <Chip tone={family ? '' : 'accent'} onClick={() => { setFamily(''); setForm(''); }}>
          All {num(data.records.length)}
        </Chip>
        {present.map((f) => {
          const count = data.records.filter((r) => r.family === f).length;
          return (
            <Chip key={f} tone={family === f ? 'accent' : ''}
                  onClick={() => { setFamily(family === f ? '' : f); setForm(''); }}>
              {familyLabel(f)} {num(count)}
            </Chip>
          );
        })}
      </div>
      <Timeline rows={data.records.filter((r) => !family || r.family === family)}
                families={family ? [family] : present} year={year} onYear={setYear} />
      <div className="toolbar" style={{ marginTop: 8 }}>
        <input type="search" placeholder="Filter these records…" value={q}
               onChange={(e) => setQ(e.target.value)} style={{ flex: 1, minWidth: 160 }} />
        {forms.length > 1 && (
          <Select label="Kind" value={form} allLabel="Every kind"
                  options={forms.map(([f, n]) => [f, `${formLabel(f)} (${n})`] as const)}
                  onChange={setForm} />
        )}
        {year !== null && <Chip tone="accent" onClick={() => setYear(null)}>{year} ×</Chip>}
      </div>
      <p className="faint" style={{ margin: '6px 0' }}>{num(rows.length)} of {num(data.records.length)} records</p>
      <RecordList rows={rows} ctx={ctx} limit={40} />
    </div>
  );
}

export function PeopleTab({ siape, ctx }: { siape: string; ctx: RecordContext }) {
  const { data, error, isPending } = usePortfolio(siape);
  if (isPending) return <Loading>Reading the portfolio…</Loading>;
  if (error || !data) return <Empty>{String((error as Error)?.message || 'No portfolio.')}</Empty>;
  const people = data.people;
  if (!people.length) return <Empty>Nobody is named on this person's records.</Empty>;
  const faculty = people.filter((p) => p.siape);
  const students = people.filter((p) => !p.siape && p.roles.includes('student'));
  const board = people.filter((p) => !p.siape && !p.roles.includes('student') && p.families.includes('committee') && !p.families.includes('work'));
  const rest = people.filter((p) => !faculty.includes(p) && !students.includes(p) && !board.includes(p));
  return (
    <div>
      <div className="grid cols-3" style={{ marginBottom: 14 }}>
        <Stat label="People named" value={num(people.length)} />
        <Stat label="Faculty among them" value={num(faculty.length)} />
        <Stat label="Students by name" value={num(students.length)} />
      </div>
      <CompanyBlock title="Colleagues in this faculty" rows={faculty} ctx={ctx}
                    note="Resolved to indexed professors: co-authors, board colleagues, team members." />
      <CompanyBlock title="Most frequent company" rows={rest} ctx={ctx} limit={25}
                    note="Co-authors and team members outside the faculty, spellings merged." />
      <CompanyBlock title="Students" rows={students} ctx={ctx} limit={25} />
      <CompanyBlock title="Met only on boards" rows={board} ctx={ctx} limit={15} />
    </div>
  );
}

function CompanyBlock({ title, rows, ctx, note, limit }: {
  title: string; rows: Company[]; ctx: RecordContext; note?: string; limit?: number;
}) {
  const [more, setMore] = useState(false);
  if (!rows.length) return null;
  const shown = limit && !more ? rows.slice(0, limit) : rows;
  return (
    <FieldBlock title={`${title} (${num(rows.length)})`} note={note}>
      <Neighbours>
        {shown.map((p) => (
          <Neighbour key={p.normalized_name}
                     title={p.display}
                     hint={[
                       p.first_year ? (p.last_year && p.last_year !== p.first_year ? `${p.first_year}–${p.last_year}` : String(p.first_year)) : null,
                       p.roles.join(', '),
                       p.families.map(familyLabel).join(', '),
                     ].filter(Boolean).join(' · ')}
                     tail={<Chip tone={p.siape ? 'accent' : ''}>{num(p.count)}</Chip>}
                     onClick={() => {
                       if (p.siape) ctx.open({ kind: 'professor', id: p.siape, label: p.display });
                       else if (p.entity_id) ctx.open({ kind: 'entity', id: p.entity_id, label: short(p.display, 24) });
                       else ctx.open({ kind: 'search', person: p.name, label: short(p.display, 24) });
                     }} />
        ))}
      </Neighbours>
      {limit && rows.length > limit && !more && (
        <button type="button" className="btn" style={{ marginTop: 6 }} onClick={() => setMore(true)}>
          Show all {num(rows.length)}
        </button>
      )}
    </FieldBlock>
  );
}

export function CareerTab({ siape, ctx }: { siape: string; ctx: RecordContext }) {
  const { data, error, isPending } = usePortfolio(siape);
  if (isPending) return <Loading>Reading the portfolio…</Loading>;
  if (error || !data) return <Empty>{String((error as Error)?.message || 'No portfolio.')}</Empty>;
  const rows = data.career;
  if (!rows.length) return <Empty>No appointments or degrees on file.</Empty>;
  const by = (family: Family) => rows
    .filter((r) => r.family === family)
    .sort((a, b) => (b.year ?? b.year_end ?? 0) - (a.year ?? a.year_end ?? 0));
  return (
    <div>
      <CareerBlock title="Appointments" rows={by('career')} ctx={ctx}
                   line={(r) => [r.nature, formLabel(r.form)].filter(Boolean).join(' · ')} />
      <CareerBlock title="Degrees" rows={by('degree')} ctx={ctx}
                   line={(r) => [formLabel(r.form), String(r.payload?.course || ''),
                                 r.counterpart ? `with ${r.counterpart}` : null].filter(Boolean).join(' · ')} />
      <CareerBlock title="Duties" rows={by('activity')} ctx={ctx}
                   line={(r) => [formLabel(r.form), String(r.payload?.unit || '')].filter(Boolean).join(' · ')} />
      <CareerBlock title="Training" rows={by('training')} ctx={ctx}
                   line={(r) => [formLabel(r.form), r.payload?.hours ? `${String(r.payload.hours)}h` : null].filter(Boolean).join(' · ')} />
    </div>
  );
}

function CareerBlock({ title, rows, ctx, line }: {
  title: string; rows: RecordDetail[]; ctx: RecordContext; line: (r: RecordDetail) => ReactNode;
}) {
  if (!rows.length) return null;
  return (
    <FieldBlock title={`${title} (${num(rows.length)})`}>
      <ol className="career">
        {rows.map((r) => (
          <li key={r.record_id} onClick={() => ctx.open(frameFor(r))}>
            <span className="span">
              {r.year ?? '?'}{r.year_end && r.year_end !== r.year ? `–${r.year_end}` : (r.status === 'current' ? '–' : '')}
            </span>
            <span className="what">
              <b>{r.family === 'career' ? r.org : (r.title || r.org)}</b>
              <span className="faint">{[line(r), r.family !== 'career' ? r.org : null].filter(Boolean).join(' · ')}</span>
            </span>
          </li>
        ))}
      </ol>
    </FieldBlock>
  );
}

/* ----------------------------------------------------------- frames */

export function RecordFrame({ id, ctx }: { id: string; ctx: RecordContext }) {
  const { data, error, isPending } = useRecord(id);
  if (isPending) return <Loading />;
  if (error || !data) return <div className="panel-body"><Empty>{String((error as Error)?.message || 'Could not load this record.')}</Empty></div>;
  return <RecordRecord detail={data} ctx={ctx} />;
}

export function EntityFrame({ id, ctx }: { id: string; ctx: RecordContext }) {
  const { data, error, isPending } = useEntity(id);
  if (isPending) return <Loading />;
  if (error || !data) return <div className="panel-body"><Empty>{String((error as Error)?.message || 'Could not load this entity.')}</Empty></div>;
  return <EntityRecord detail={data} ctx={ctx} />;
}

/** Every record matching a filter, as a frame: how "everything naming this
 * person" opens without leaving the screen. */
export function SearchFrame({ frame, ctx }: {
  frame: Extract<Frame, { kind: 'search' }>; ctx: RecordContext;
}) {
  const { data, error, isPending } = useRecords({
    person: frame.person, q: frame.q, family: frame.family, limit: 300,
  });
  if (isPending) return <Loading />;
  if (error || !data) return <div className="panel-body"><Empty>{String((error as Error)?.message || 'Nothing.')}</Empty></div>;
  return (
    <div>
      <div className="detail-head">
        <h2>{frame.person ? `Records naming ${frame.person}` : `Records matching “${frame.q}”`}</h2>
        <p className="sub">{num(data.total)} record{data.total === 1 ? '' : 's'}{frame.family ? ` · ${familyLabel(frame.family)}` : ''}</p>
        <div className="detail-chips">
          {ctx.here !== 'explore' && (
            <LeaveLink label="Explore screen"
                       to={`/explore?${frame.person ? `person=${encodeURIComponent(frame.person)}` : `q=${encodeURIComponent(frame.q || '')}`}`} />
          )}
        </div>
      </div>
      <div className="panel-body">
        <RecordList rows={data.rows} ctx={ctx} showSubject limit={60} />
      </div>
    </div>
  );
}
