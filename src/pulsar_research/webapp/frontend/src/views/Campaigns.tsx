/* Outreach — the audience, the individual drafts, and where each thread stands.
 *
 * The console used to stop at "sent: 32". Sending is the cheap half: what
 * decides the outcome is which of those conversations is still alive a week
 * later, and which single one receives the SIGAA indication. So this view is a
 * board over the campaign, with the funnel as its primary filter and a note
 * field per professor, and the draft is shown beside the reasoning that
 * produced it.
 *
 * There is deliberately no send button. A local page that can put 32 messages
 * in front of 32 professors on one click is one stray click from an
 * irreversible mistake; the exact command is offered for copying instead.
 */

import { useEffect, useMemo, useState } from 'react';
import { useAppState, useCampaign, useSaveOutcome } from '../api/client';
import type { CampaignDetail, RecipientRow, ThreadState } from '../api/types';
import { useRail, type RailStack } from '../components/Rail';
import { RailPane } from '../components/RailPane';
import { copyText, notify, stateTone } from '../components/records';
import { Table } from '../components/Table';
import {
  Banner, Chip, Empty, FieldBlock, Loading, Meter, Panel, Tabs,
} from '../components/ui';
import { ago, date, num, short, shortName, text } from '../lib/format';
import { useViewParams } from '../lib/params';
import { HeaderActions } from '../shell/Shell';

const TABS = ['thread', 'message', 'why'] as const;
type Tab = typeof TABS[number];

export function Campaigns() {
  const params = useViewParams();
  const rail = useRail();
  const { data: state } = useAppState();
  const list = state?.campaigns || [];
  const chosen = params.get('c') || list[0]?.campaign_id || '';
  const { data, error, isPending } = useCampaign(chosen || null);

  const filters = {
    state: params.get('state'),
    q: params.get('q'),
    sel: params.get('sel'),
  };

  const rows = useMemo(() => {
    if (!data) return [];
    const needle = filters.q.trim().toLowerCase();
    return data.rows.filter((row) => {
      if (filters.state && (row.state || '') !== filters.state) return false;
      if (needle) {
        const hay = [row.professor_name, row.email, row.primary_title, row.note]
          .join(' ').toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
  }, [data, filters.state, filters.q]);

  if (!list.length) {
    return (
      <Banner tone="info">
        <div>
          <b>No campaigns yet. </b>
          Build one with <code className="mono">pulsar campaign create "&lt;name&gt;" --funded</code>.
          {' '}Audiences are frozen at creation, so a later corpus change cannot rewrite
          why someone was contacted.
        </div>
      </Banner>
    );
  }
  if (isPending) return <Loading />;
  if (error || !data) {
    return <Banner tone="bad"><div>{String((error as Error)?.message || error)}</div></Banner>;
  }

  const indicated = data.rows.find((r) => r.state === 'indicated');

  return (
    <div>
      <HeaderActions>
        <select style={{ width: 'auto' }} value={chosen}
                onChange={(e) => params.set({ c: e.target.value, sel: '', state: '' }, { push: true })}>
          {list.map((c) => (
            <option key={c.campaign_id} value={c.campaign_id}>
              {c.name} · {c.sent}/{c.recipients} sent
            </option>
          ))}
        </select>
        <button type="button" className="btn sm"
                onClick={() => copyText(data.send_command, 'Send command copied')}>
          Copy send command
        </button>
      </HeaderActions>

      <div className="split">
        <div>
          {indicated && (
            <Banner tone="info">
              <div>
                <b>Indicated: </b>{indicated.professor_name}. The SIGAA indication is unique,
                so no other thread in this campaign can take one.
              </div>
            </Banner>
          )}

          <div className="funnel" style={{ marginBottom: '14px' }}>
            <button type="button" aria-pressed={filters.state === ''}
                    onClick={() => params.set({ state: '' })}>
              <span className="n">{num(data.rows.length)}</span>
              <span className="k">All recipients</span>
            </button>
            {data.states.map(({ key, label }) => (
              <button key={key} type="button" aria-pressed={filters.state === key}
                      onClick={() => params.set({ state: key })}>
                <span className="n">{num(data.funnel[key] ?? 0)}</span>
                <span className="k">{label}</span>
              </button>
            ))}
          </div>

          <div className="toolbar">
            <label className="field grow">
              Search recipients
              <input type="search" value={filters.q} data-role="search"
                     placeholder="Name, e-mail, plan or note…"
                     onChange={(e) => params.set({ q: e.target.value })} />
            </label>
          </div>

          <Panel flush>
            <Table rows={rows} rowKey={(r) => r.siape} selected={filters.sel}
                   maxHeight="calc(100vh - 430px)"
                   emptyMessage="No recipient matches this filter."
                   onSelect={(row) => { rail.reset(); params.set({ sel: row.siape }); }}
                   columns={[
                     { key: 'professor_name', label: 'Professor', truncate: true,
                       render: (r) => (
                         <div>
                           <div>{text(r.professor_name)}</div>
                           <div className="faint" style={{ fontSize: '11.5px' }}>
                             {text(r.email)}
                           </div>
                         </div>
                       ) },
                     { key: 'state', label: 'State', width: '162px', sortable: false,
                       render: (r) => <StateSelect campaign={data} row={r} /> },
                     { key: 'opportunity_percentile', label: 'Fit', align: 'right', width: '104px',
                       render: (r) => <Meter value={r.opportunity_percentile} /> },
                     { key: 'sent_at', label: 'Sent', width: '96px',
                       render: (r) => (r.sent_at
                         ? <span className="faint" title={date(r.sent_at)}>{ago(r.sent_at)}</span>
                         : <Chip tone="warn">{text(r.status, 'draft')}</Chip>) },
                   ]} />
          </Panel>
        </div>

        <RailPane stack={rail} here="campaigns" rootLabel="This recipient"
                  root={<RecipientPane campaign={data} siape={filters.sel} rail={rail}
                                       tab={params.pick<Tab>('tab', TABS, 'thread')}
                                       onTab={(tab) => params.set({ tab })} />} />
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- state */

function StateSelect({ campaign, row }: { campaign: CampaignDetail; row: RecipientRow }) {
  const save = useSaveOutcome(campaign.campaign_id);
  if (!row.state) return <span className="faint">—</span>;
  return (
    <select className={`state-select state-${row.state}`}
            value={row.state}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => {
              const next = e.target.value as ThreadState;
              save.mutate({ siape: row.siape, state: next }, {
                onSuccess: () => notify('Saved'),
                onError: (err) => notify(String(err.message), 'bad'),
              });
            }}>
      {campaign.states.map(({ key, label }) => (
        <option key={key} value={key}>{label}</option>
      ))}
    </select>
  );
}

/* ---------------------------------------------------------------- detail */

function RecipientPane({ campaign, siape, rail, tab, onTab }: {
  campaign: CampaignDetail; siape: string; rail: RailStack;
  tab: Tab; onTab: (tab: Tab) => void;
}) {
  const row = campaign.rows.find((r) => r.siape === siape);
  if (!row) {
    return (
      <div className="panel-body">
        <Empty>Select a recipient to read their draft and the reasoning behind it.</Empty>
      </div>
    );
  }

  const label = (key: string) =>
    campaign.states.find((s) => s.key === key)?.label || key || '—';

  return (
    <div>
      <div className="detail-head">
        <h2>{text(row.professor_name)}</h2>
        <p className="sub">
          {[row.email, row.sent_at ? `sent ${date(row.sent_at)}` : row.status]
            .filter(Boolean).join(' · ')}
        </p>
        <div className="detail-chips">
          {row.state
            ? <Chip tone={stateTone(row.state)}>{label(row.state)}</Chip>
            : <Chip tone="warn">Not sent</Chip>}
          {row.opportunity_percentile !== null && row.opportunity_percentile !== undefined && (
            <Chip tone="accent">fit p{num(row.opportunity_percentile, 0)}</Chip>
          )}
          {row.funded_slots ? <Chip tone="good">{num(row.funded_slots)} funded slots</Chip> : null}
          {row.is_customized && <Chip>Draft edited by hand</Chip>}
          {/* The supervisor opens here; the audience, the filter and the draft
              being reviewed all stay where they are. */}
          <Chip tone="accent"
                onClick={() => rail.push({
                  kind: 'professor', id: row.siape, label: shortName(row.professor_name),
                })}>
            The supervisor
          </Chip>
        </div>
      </div>
      <Tabs items={[
        ['thread', 'Thread'],
        ['message', 'Message sent'],
        ['why', 'Why they qualified'],
      ] as const} active={tab} onChange={onTab} />
      <div className="panel-body">
        {tab === 'thread' && <Thread campaign={campaign} row={row} />}
        {tab === 'message' && <Message row={row} />}
        {tab === 'why' && <Why row={row} rail={rail} />}
      </div>
    </div>
  );
}

function Message({ row }: { row: RecipientRow }) {
  return (
    <div>
      <FieldBlock title="Subject">
        <div className="prose">{text(row.subject)}</div>
      </FieldBlock>
      <FieldBlock title={
        <>
          Body as delivered
          <button type="button" className="btn sm"
                  style={{ float: 'right', marginTop: '-4px' }}
                  onClick={() => copyText(row.body_text || '', 'Message copied')}>
            Copy
          </button>
        </>
      }>
        <pre className="pre" style={{ maxHeight: '520px' }}>{text(row.body_text)}</pre>
      </FieldBlock>
    </div>
  );
}

function Why({ row, rail }: { row: RecipientRow; rail: RailStack }) {
  const evidence = row.qualifying_evidence || [];
  return (
    <div>
      <p className="panel-note" style={{ marginBottom: '12px' }}>
        Frozen when the campaign was created. A later corpus rebuild cannot rewrite the
        reason someone was contacted, which is what makes this record worth keeping.
      </p>
      {row.matched_skills?.length ? (
        <FieldBlock title="Matched techniques">
          <div className="chips">
            {row.matched_skills.map((s) => <Chip key={s}>{s}</Chip>)}
          </div>
        </FieldBlock>
      ) : null}
      {evidence.length
        ? (
          <FieldBlock title="Qualifying work plans">
            <Table rows={evidence} rowKey={(e) => e.id_opportunity}
                   onSelect={(e) => rail.push({
                     kind: 'plan', id: e.id_opportunity,
                     label: short(e.plan_title || e.project_title, 26),
                   })}
                   columns={[
                     { key: 'plan_title', label: 'Work plan', truncate: true,
                       render: (e) => short(e.plan_title || e.project_title, 58) },
                     { key: 'funded_slots', label: 'Slots', align: 'right', width: '64px',
                       render: (e) => num(e.funded_slots) },
                     { key: 'opportunity_percentile', label: 'Fit', align: 'right', width: '96px',
                       render: (e) => <Meter value={e.opportunity_percentile} /> },
                   ]} />
          </FieldBlock>
        )
        : <Empty>No frozen evidence on this recipient.</Empty>}
      <FieldBlock title="Full rationale">
        <pre className="pre">{JSON.stringify(row.rationale, null, 2)}</pre>
      </FieldBlock>
    </div>
  );
}

function Thread({ campaign, row }: { campaign: CampaignDetail; row: RecipientRow }) {
  const save = useSaveOutcome(campaign.campaign_id);
  const [note, setNote] = useState(row.note || '');

  // A different recipient is a different note; without this the textarea keeps
  // the previous person's text and one careless save writes it onto them.
  useEffect(() => { setNote(row.note || ''); }, [row.siape, row.note]);

  if (!row.state) {
    return (
      <div>
        <Banner tone="warn">
          <div>
            <b>Not sent. </b>
            Conversation state only exists for messages that actually went out.
          </div>
        </Banner>
        <FieldBlock title="Deliver this campaign">
          <p className="pre">pulsar campaign send {campaign.campaign_id} --confirm</p>
        </FieldBlock>
      </div>
    );
  }

  const label = (key: string) =>
    campaign.states.find((s) => s.key === key)?.label || key;

  const commit = (patch: { state?: ThreadState; note?: string }) => {
    save.mutate({ siape: row.siape, ...patch }, {
      onSuccess: () => notify('Saved'),
      onError: (err) => notify(String(err.message), 'bad'),
    });
  };

  return (
    <div>
      <dl className="kv" style={{ marginBottom: '14px' }}>
        <dt>State</dt><dd>{label(row.state)}</dd>
        <dt>Sent</dt><dd>{date(row.sent_at)}</dd>
        <dt>First reply</dt><dd>{row.replied_at ? date(row.replied_at) : '—'}</dd>
        <dt>Plan</dt><dd>{short(row.primary_title, 70)}</dd>
      </dl>
      <FieldBlock title="Move this conversation">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {campaign.states.map(({ key, label: text_ }) => (
            <button key={key} type="button"
                    className={`btn sm${key === row.state ? ' primary' : ''}`}
                    disabled={save.isPending}
                    onClick={() => commit({ state: key, note })}>
              {text_}
            </button>
          ))}
        </div>
      </FieldBlock>
      <FieldBlock title="Notes">
        <textarea rows={7} value={note} onChange={(e) => setNote(e.target.value)}
                  placeholder="What they said, what was agreed, what to do next…" />
        <button type="button" className="btn sm" style={{ marginTop: '8px' }}
                disabled={save.isPending}
                onClick={() => commit({ note })}>
          Save note
        </button>
      </FieldBlock>
    </div>
  );
}
